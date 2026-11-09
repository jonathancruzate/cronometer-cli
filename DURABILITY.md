# Cronometer CLI durability — report (2026-11-09)

## What kills the session

The token itself has a **fixed ~7–8 day lifetime from mint**, and nothing in the shipped CLI could extend or renew it:

- Mint #1 (full login, 2026-10-18 ~20:25) was already dead by 2026-10-26 (README §5b records `auth status` → "Token Authorization failed") — ≤8 days, despite daily use in between. Expiry is **not** inactivity-based and **not** extended by use.
- Mint #2 (2026-10-31 19:57 EST — this is what refreshed `session.json`; the Oct 31 daily log shows a full `auth login` re-run with the password pulled through the Vaultwarden lane to log the afternoon chocolates, and `session.json`'s `updatedAt` of `2026-10-31T23:57:17Z` matches to the minute) was dead by 2026-11-09. The live keepalive probe measured its age at death-observation as **8.27 days**.
- **Resume/rotation:** Per AUTH-FINDINGS-2.md §1, both `loginWithEmailAndPw` and `loginWithSessionKey` funnel into `_loginAndUpdateUser`, and the app re-stores whatever `sessionKey` the response carries via `User::initialize` — i.e., the login endpoint is the single mint point and a resume response may carry a rotated key. **crono.py's `check_status` detected a `sessionKey` in the resume response but never persisted it** — any server-side rotation was silently discarded. No other code path persisted rotations either. Defect confirmed in code.
- **Full login vs resume:** A full `auth login` mints a brand-new key with a fresh TTL (both observed recoveries were full logins). Resume only validates (and at best rotates); as shipped it could neither revive a dead token nor be relied on to extend one. The vendor's own durability design is documented in AUTH-FINDINGS.md: the app's reauthenticator persists credentials and re-runs `loginWithEmailAndPw` when token resume fails. That is the design mirrored here.
- Enablers: the account's email is on file locally and **2FA is off** (`~/workspace/nutrition/cronometer-notes.md`), so password-only re-login works with no TOTP blocker. No credentials file existed yet (`~/.config/cronometer-cli/` held only `session.json`); precedent for the pattern exists at `~/.config/agent-google/password` (0600).

## The mechanism built (all in `~/workspace/cronometer-cli/`)

1. **Rotation persistence (`crono.py`, `check_status`)** — if the resume response carries a different `sessionKey` (or userId), it is saved via `save_session_data` and reported as `rotated: true`; full logins and rotations now also stamp `lastLoginAt` in the session file.
2. **`auth status` heals in place (`_heal_status`)** — a failed resume triggers one automatic re-login from stored credentials, then a fresh check; the result reports `healed: true` (or `healError`). Lanes that gate on `auth status` (the food-logging skill) recover with **no invocation-contract change**.
3. **Auto-relogin + retry (`main`)** — any non-`auth` command failing with an auth error (`Token Authorization failed` / `Authentication Failed` / HTTP 401–403 "session missing or expired") is retried once after an automatic re-login, with a stderr notice. Safe against double-posting: auth failures are raised by the server-side auth filter *before* endpoint dispatch (AUTH-FINDINGS-2.md §2.3), so the failed call never executed. Kill switch: `CRONO_NO_AUTOLOGIN=1`.
4. **`keepalive.py`** — daily driver: resume check → **proactive re-mint when the token is older than 5 days** (safely inside the ~7–8 day lifetime, so logging lanes never meet a dead token) → heal-by-relogin if the resume already failed. Writes only timestamps/booleans to `~/.config/cronometer-cli/keepalive-state.json` (0600). Exit codes: 0 healthy, 2 still dead after re-login, 3 dead with no stored credentials (prints the exact one-time step on stderr).
5. **Credentials source** — `CRONOMETER_USER`/`CRONOMETER_PASS` env first, else the 0600 file `~/.config/cronometer-cli/credentials` (`email=` / `password=` lines). The loader **refuses files with group/other permission bits**. The password goes only to the login call — never printed, logged, or stored elsewhere. Template committed as `credentials.example`. The password was not fetched from the Secure Vault, Google Password Manager, or Vaultwarden.

## What you must do — exactly once

Create the credentials file (the email is `you@example.com` per local notes):

```bash
printf 'email=you@example.com\npassword=<cronometer password>\n' > ~/.config/cronometer-cli/credentials
chmod 600 ~/.config/cronometer-cli/credentials
```

From that moment the CLI is self-healing: commands retry after auto-relogin, `auth status` heals, and the daily keepalive re-mints before expiry. Until then, everything degrades gracefully — commands fail with the same errors as today, and the keepalive exits 3 naming this step. The currently-dead session also revives automatically on the first healed command once the file exists.

## Verification evidence (no live login attempted)

- **Unit tests: 23/23 pass** (`python3 -m unittest discover -s tests`) — the original 11, plus 8 new durability tests in `tests/test_crono.py` (rotation persisted / not-rotated, auth-failure classifier, credentials parsing + 0600 refusal + env precedence, `main` retries exactly once after re-login and lands the write flow, no-credentials → no retry + exit 1, `auth status` heals via main) and 4 in `tests/test_keepalive.py` (heal dead session, proactive re-mint at 6 days, young token left alone, dead-without-credentials → exit 3). One test caught a real reporting bug during development (post-remint age overwrote the decision age) — fixed by recording `sessionAgeDaysAfter` separately.
- **Dry-run:** `diary add --dry-run` output unchanged, token redacted as before.
- **Live read-only probes against the real dead session:** `crono.py --json auth status` → `loggedIn: false`, "Token Authorization failed", rc 0, no heal attempted (no credentials). `keepalive.py` → `sessionAgeDays: 8.27`, `credentialsAvailable: false`, exit 3 with the one-time-step message on stderr, state file written. Exactly as designed.

## Registration (completed by main agent, 2026-11-09)

The subagent's cron registration was refused by the safety policy in its session; the keep-alive job `cronometer-cli-keepalive` (owner goal:stay-lean, daily ~05:37 America/New_York, body per the build spec) was registered by the main agent in the same handling.
