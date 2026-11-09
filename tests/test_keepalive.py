"""Offline unit tests for keepalive.py (mocked network, no live calls)."""

from __future__ import annotations

import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import requests

import crono
import keepalive
from test_crono import make_mock_response

OLD_KEY = "old_key_for_keepalive_tests_0000"
NEW_KEY = "new_key_minted_by_keepalive_0000"
NOW = datetime.datetime(2026, 11, 9, 6, 0, tzinfo=datetime.timezone.utc)


class TestKeepalive(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.tmpdir.name) / "session.json"
        self.state_file = Path(self.tmpdir.name) / "keepalive-state.json"
        self.creds_file = Path(self.tmpdir.name) / "credentials"
        self._env_keys = (
            "CRONOMETER_USER",
            "CRONOMETER_PASS",
            "CRONOMETER_CREDENTIALS_FILE",
            "CRONO_NO_AUTOLOGIN",
        )
        self._saved_env = {k: os.environ.get(k) for k in self._env_keys}
        os.environ.pop("CRONOMETER_USER", None)
        os.environ.pop("CRONOMETER_PASS", None)
        os.environ.pop("CRONO_NO_AUTOLOGIN", None)
        os.environ["CRONOMETER_CREDENTIALS_FILE"] = str(self.creds_file)

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmpdir.cleanup()

    def _save_session(self, last_login_at: str | None = None) -> None:
        meta = {"lastLoginAt": last_login_at} if last_login_at else None
        crono.save_session_data(self.session_file, user_id=111222, session_key=OLD_KEY, meta=meta)

    def _write_creds(self) -> None:
        self.creds_file.write_text("email=u@example.com\npassword=file_pw\n", encoding="utf-8")
        os.chmod(self.creds_file, 0o600)

    def _run(self) -> dict:
        return keepalive.run_keepalive(
            session_file=str(self.session_file),
            state_file=str(self.state_file),
            now=NOW,
        )

    def test_heals_dead_session(self) -> None:
        self._save_session()
        self._write_creds()
        calls = {"resume": 0}

        def fake_request(**kwargs: object) -> mock.MagicMock:
            url = str(kwargs.get("url", ""))
            body = kwargs.get("json") or {}
            if url.endswith("/login") and "email" in body:
                return make_mock_response(200, json_data={"id": 111222, "sessionKey": NEW_KEY})
            if url.endswith("/login"):
                calls["resume"] += 1
                if calls["resume"] == 1:
                    return make_mock_response(200, json_data={"error": "Token Authorization failed"})
                return make_mock_response(200, json_data={"id": 111222, "sessionKey": NEW_KEY})
            raise AssertionError(f"unexpected URL {url}")

        with mock.patch.object(requests.Session, "request", side_effect=fake_request):
            summary = self._run()

        self.assertTrue(summary["loggedIn"])
        self.assertTrue(summary["healed"])
        self.assertFalse(summary["reminted"])
        self.assertTrue(summary["credentialsAvailable"])
        self.assertEqual(crono.load_session_data(self.session_file)["sessionKey"], NEW_KEY)
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertTrue(state["loggedIn"])
        self.assertNotIn(NEW_KEY, self.state_file.read_text(encoding="utf-8"))

    def test_proactive_remint_before_expiry(self) -> None:
        six_days_ago = (NOW - datetime.timedelta(days=6)).isoformat()
        self._save_session(last_login_at=six_days_ago)
        self._write_creds()

        def fake_request(**kwargs: object) -> mock.MagicMock:
            url = str(kwargs.get("url", ""))
            body = kwargs.get("json") or {}
            if url.endswith("/login") and "email" in body:
                return make_mock_response(200, json_data={"id": 111222, "sessionKey": NEW_KEY})
            if url.endswith("/login"):
                # Resume succeeds; the key it echoes depends on whether the
                # re-mint already happened (session file holds NEW_KEY then).
                current = crono.load_session_data(self.session_file).get("sessionKey", OLD_KEY)
                return make_mock_response(200, json_data={"id": 111222, "sessionKey": current})
            raise AssertionError(f"unexpected URL {url}")

        with mock.patch.object(requests.Session, "request", side_effect=fake_request):
            summary = self._run()

        self.assertTrue(summary["loggedIn"])
        self.assertTrue(summary["reminted"])
        self.assertFalse(summary["healed"])
        self.assertGreater(summary["sessionAgeDays"], 5)
        self.assertLess(summary["sessionAgeDaysAfter"], 1)
        self.assertEqual(crono.load_session_data(self.session_file)["sessionKey"], NEW_KEY)

    def test_young_token_left_alone(self) -> None:
        one_day_ago = (NOW - datetime.timedelta(days=1)).isoformat()
        self._save_session(last_login_at=one_day_ago)
        self._write_creds()
        calls = {"login": 0}

        def fake_request(**kwargs: object) -> mock.MagicMock:
            url = str(kwargs.get("url", ""))
            body = kwargs.get("json") or {}
            if url.endswith("/login") and "email" in body:
                calls["login"] += 1
                return make_mock_response(200, json_data={"id": 111222, "sessionKey": NEW_KEY})
            if url.endswith("/login"):
                return make_mock_response(200, json_data={"id": 111222, "sessionKey": OLD_KEY})
            raise AssertionError(f"unexpected URL {url}")

        with mock.patch.object(requests.Session, "request", side_effect=fake_request):
            summary = self._run()

        self.assertTrue(summary["loggedIn"])
        self.assertFalse(summary["reminted"])
        self.assertEqual(calls["login"], 0)
        self.assertEqual(crono.load_session_data(self.session_file)["sessionKey"], OLD_KEY)

    def test_dead_without_credentials_exit_3(self) -> None:
        self._save_session()
        # No credentials file written -> credentialsAvailable False.

        def fake_request(**kwargs: object) -> mock.MagicMock:
            return make_mock_response(200, json_data={"error": "Token Authorization failed"})

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(requests.Session, "request", side_effect=fake_request):
            with redirect_stdout(out), redirect_stderr(err):
                rc = keepalive.main(
                    ["--session-file", str(self.session_file), "--state-file", str(self.state_file)]
                )

        self.assertEqual(rc, 3)
        self.assertIn("chmod 600", err.getvalue())
        summary = json.loads(out.getvalue())
        self.assertFalse(summary["loggedIn"])
        self.assertFalse(summary["credentialsAvailable"])


if __name__ == "__main__":
    unittest.main()
