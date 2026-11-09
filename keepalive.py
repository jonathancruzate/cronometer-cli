#!/usr/bin/env python3
"""keepalive — session durability driver for the Cronometer CLI (crono.py).

Why this exists (see DURABILITY.md): a Cronometer mobile sessionKey minted
by a full email+password login lives only ~7-8 days and then dies with
"Token Authorization failed", no matter how often it is used or resumed.
This script runs daily (cron: cronometer-cli-keepalive) and keeps the CLI
logged in forever:

  1. Resume check (`CronoClient.check_status`) — which now also persists a
     rotated sessionKey whenever the server issues one.
  2. Proactive re-mint — if the token is older than --max-age-days
     (default 5, safely inside the observed ~7-8 day lifetime) and stored
     credentials exist, re-run the full login BEFORE the token dies, so
     the logging lanes never see a dead session.
  3. Heal — if the resume check fails outright, re-login from stored
     credentials and re-check.

Credentials come from CRONOMETER_USER / CRONOMETER_PASS, else the 0600
file ~/.config/cronometer-cli/credentials (see crono.stored_credentials).
The password is never printed or written anywhere; the state file holds
only timestamps and booleans.

Exit codes: 0 = session healthy (possibly rotated / re-minted / healed);
2 = session still dead after every available self-heal path;
3 = session dead and no stored credentials exist (a human must create the
credentials file once — the exact step is printed on stderr).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crono  # noqa: E402

DEFAULT_STATE_FILE = "~/.config/cronometer-cli/keepalive-state.json"
DEFAULT_MAX_AGE_DAYS = 5.0


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value: Any) -> Optional[datetime.datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def session_age_days(session_path: Path, now: datetime.datetime) -> Optional[float]:
    """Age of the current mint: lastLoginAt if recorded, else updatedAt."""
    data = crono.load_session_data(session_path)
    stamp = _parse_iso(data.get("lastLoginAt")) or _parse_iso(data.get("updatedAt"))
    if stamp is None:
        return None
    return max(0.0, (now - stamp).total_seconds() / 86400.0)


def run_keepalive(
    session_file: Optional[str] = None,
    state_file: Optional[str] = None,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    now = now or _utcnow()
    client = crono.CronoClient(session_file=session_file)
    creds: Optional[tuple] = None
    creds_error: Optional[str] = None
    if not crono.autologin_disabled():
        try:
            creds = crono.stored_credentials()
        except crono.CronoError as exc:
            creds_error = str(exc)

    status = client.check_status()
    summary: Dict[str, Any] = {
        "ranAt": now.isoformat(),
        "loggedIn": bool(status.get("loggedIn")),
        "rotated": bool(status.get("rotated")),
        "reminted": False,
        "healed": False,
        "credentialsAvailable": creds is not None,
        "sessionAgeDays": session_age_days(client.session_path, now),
    }
    if status.get("error"):
        summary["statusError"] = status["error"]
    if creds_error:
        summary["credentialsError"] = creds_error

    age = summary["sessionAgeDays"]
    needs_remint = bool(status.get("loggedIn")) and age is not None and age > max_age_days
    needs_heal = not status.get("loggedIn")

    if (needs_remint or needs_heal) and creds is not None:
        try:
            client.login(email=creds[0], password=creds[1])
            status = client.check_status()
            summary["loggedIn"] = bool(status.get("loggedIn"))
            summary["rotated"] = bool(status.get("rotated"))
            summary["reminted"] = needs_remint and summary["loggedIn"]
            summary["healed"] = needs_heal and summary["loggedIn"]
            # sessionAgeDays stays the age observed at check time (it drove
            # the decision); the post-login age lands in its own field.
            summary["sessionAgeDaysAfter"] = session_age_days(client.session_path, now)
            if status.get("error"):
                summary["statusError"] = status["error"]
        except crono.CronoError as exc:
            summary["loginError"] = str(exc)

    _write_state(state_file, summary)
    return summary


def _write_state(state_file: Optional[str], summary: Dict[str, Any]) -> None:
    path = Path(state_file or os.environ.get("CRONO_KEEPALIVE_STATE") or DEFAULT_STATE_FILE).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, sort_keys=True)
            f.write("\n")
    finally:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(prog="keepalive", description="Cronometer CLI session keep-alive")
    parser.add_argument("--session-file", default=None, help="Override crono session file path")
    parser.add_argument("--state-file", default=None, help="Override keepalive state file path")
    parser.add_argument(
        "--max-age-days",
        type=float,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Proactively re-mint the token when it is older than this (default: 5)",
    )
    args = parser.parse_args(argv)

    summary = run_keepalive(
        session_file=args.session_file,
        state_file=args.state_file,
        max_age_days=args.max_age_days,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["loggedIn"]:
        return 0
    if not summary["credentialsAvailable"]:
        cred_path = crono.resolve_credentials_path()
        print(
            "Session is dead and no stored credentials exist. One-time human step: "
            f"create {cred_path} containing two lines 'email=<cronometer email>' and "
            "'password=<cronometer password>', then run: chmod 600 "
            f"{cred_path} — after that this keep-alive self-heals forever.",
            file=sys.stderr,
        )
        return 3
    print("Session still dead after automatic re-login attempt.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
