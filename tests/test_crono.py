"""Offline unit tests for crono.py using unittest.mock (no live network calls)."""

from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import requests

import crono


def make_mock_response(
    status_code: int = 200,
    json_data: object = None,
    text: str | None = None,
    cookies: dict[str, str] | None = None,
) -> mock.MagicMock:
    resp = mock.MagicMock(spec=requests.Response)
    resp.status_code = status_code
    if text is not None:
        resp.text = text
        resp.json.side_effect = ValueError("No JSON object could be decoded")
    elif json_data is not None:
        resp.text = json.dumps(json_data)
        resp.json.return_value = json_data
    else:
        resp.text = ""
        resp.json.side_effect = ValueError("Empty body")
    resp.cookies = cookies or {}
    return resp


class TestCronoSessionAndAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.tmpdir.name) / "subdir" / "session.json"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_session_save_and_load_permissions(self) -> None:
        crono.save_session_data(
            self.session_file,
            user_id=1234567,
            session_key="0123456789abcdef0123456789abcdef",
            cookies={"AWSALB": "alb_val"},
        )
        self.assertTrue(self.session_file.exists())

        mode = stat.S_IMODE(os.stat(self.session_file).st_mode)
        self.assertEqual(mode, 0o600)

        loaded = crono.load_session_data(self.session_file)
        self.assertEqual(loaded["userId"], 1234567)
        self.assertEqual(loaded["sessionKey"], "0123456789abcdef0123456789abcdef")
        self.assertEqual(loaded["cookies"], {"AWSALB": "alb_val"})
        self.assertIn("updatedAt", loaded)

        client = crono.CronoClient(session_file=str(self.session_file))
        self.assertEqual(client.user_id, 1234567)
        self.assertEqual(client.session_key, "0123456789abcdef0123456789abcdef")
        self.assertEqual(client.session.cookies.get("AWSALB"), "alb_val")

    def test_v2_auth_login_payload_and_hides_secrets(self) -> None:
        secret_key = "abcd1234abcd1234abcd1234abcd1234"
        login_resp = make_mock_response(
            status_code=200,
            json_data={
                "id": 7654321,
                "sessionKey": secret_key,
                "serviceLevel": "GOLD",
                "pro": False,
            },
        )

        with mock.patch.object(requests.Session, "request", return_value=login_resp) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            res = client.login(email="test_user@example.invalid", password="super_secret_password", totp="654321")

            mock_req.assert_called_once()
            call_kwargs = mock_req.call_args.kwargs
            self.assertEqual(call_kwargs["method"], "POST")
            self.assertEqual(call_kwargs["url"], "https://mobile.cronometer.com/api/v2/login")
            self.assertEqual(call_kwargs["timeout"], 20)

            sent_body = call_kwargs["json"]
            self.assertEqual(sent_body["email"], "test_user@example.invalid")
            self.assertEqual(sent_body["password"], "super_secret_password")
            self.assertEqual(sent_body["userCode"], "654321")
            self.assertEqual(sent_body["build"], "4.58.3 b3541-a")
            self.assertIn("device", sent_body)
            self.assertIn("timezone", sent_body)
            self.assertIsNone(sent_body["firebaseToken"])
            self.assertEqual(
                sent_body["auth"],
                {
                    "userId": None,
                    "token": None,
                    "api": 3,
                    "os": "Android",
                    "build": "3541",
                    "flavour": None,
                },
            )
            self.assertEqual(sent_body["lastSeen"], 0)
            self.assertEqual(sent_body["config"], {"call_version": 4})

        self.assertTrue(res["success"])
        self.assertEqual(res["userId"], 7654321)
        self.assertNotIn("sessionKey", res)

        saved = crono.load_session_data(self.session_file)
        self.assertEqual(saved["userId"], 7654321)
        self.assertEqual(saved["sessionKey"], secret_key)

        buf = io.StringIO()
        with redirect_stdout(buf):
            crono.format_auth_login(res)
        out = buf.getvalue()
        self.assertIn("7654321", out)
        self.assertNotIn(secret_key, out)
        self.assertNotIn("super_secret_password", out)

    def test_auth_web_login_extracts_anticsrf_and_hides_cookie_values(self) -> None:
        html = '<html><form><input type="hidden" name="anticsrf" value="csrf_tok_999"/></form></html>'
        get_resp = make_mock_response(status_code=200, text=html)
        post_resp = make_mock_response(status_code=200, json_data={"success": True})

        with (
            mock.patch.object(requests.Session, "get", return_value=get_resp) as mock_get,
            mock.patch.object(requests.Session, "post") as mock_post,
        ):
            client = crono.CronoClient(session_file=str(self.session_file))

            def fake_post(url: str, data: dict[str, str], headers: dict[str, str], **kwargs: object) -> mock.MagicMock:
                client.session.cookies.set("JSESSIONID", "secret_cookie_do_not_print")
                client.session.cookies.set("sesnonce", "secret_nonce_do_not_print")
                return post_resp

            mock_post.side_effect = fake_post

            res = client.web_login(username="test_user@example.invalid", password="test_pass_value", totp="123456")

            mock_get.assert_called_once_with("https://cronometer.com/login/", timeout=20)
            mock_post.assert_called_once_with(
                "https://cronometer.com/login",
                data={
                    "username": "test_user@example.invalid",
                    "password": "test_pass_value",
                    "anticsrf": "csrf_tok_999",
                    "userCode": "123456",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=20,
                allow_redirects=False,
            )

        self.assertTrue(res["success"])
        self.assertEqual(res["cookieNames"], ["JSESSIONID", "sesnonce"])

        buf = io.StringIO()
        with redirect_stdout(buf):
            crono.format_auth_login(res)
        out = buf.getvalue()
        self.assertIn("JSESSIONID", out)
        self.assertNotIn("secret_cookie_do_not_print", out)
        self.assertNotIn("test_pass_value", out)

    def test_auth_status_session_resume(self) -> None:
        crono.save_session_data(
            self.session_file,
            user_id=987654,
            session_key="saved_secret_token_32_chars_0000",
        )
        resume_resp = make_mock_response(
            status_code=200,
            json_data={"id": 987654, "sessionKey": "saved_secret_token_32_chars_0000", "serviceLevel": "GOLD"},
        )

        with mock.patch.object(requests.Session, "request", return_value=resume_resp) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            status = client.check_status()

            self.assertTrue(status["loggedIn"])
            self.assertEqual(status["userId"], 987654)
            self.assertEqual(status["serviceLevel"], "GOLD")

            mock_req.assert_called_once()
            sent_body = mock_req.call_args.kwargs["json"]
            self.assertNotIn("email", sent_body)
            self.assertNotIn("password", sent_body)
            self.assertEqual(sent_body["auth"]["userId"], 987654)
            self.assertEqual(sent_body["auth"]["token"], "saved_secret_token_32_chars_0000")
            self.assertEqual(sent_body["config"], {"call_version": 4})


class TestCronoDiaryAndPayloads(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.tmpdir.name) / "session.json"
        crono.save_session_data(
            self.session_file,
            user_id=111222,
            session_key="sess_key_for_unit_tests_12345678",
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_get_diary_parsing_and_food_resolution(self) -> None:
        get_diary_resp = {
            "diary": [
                {
                    "measureId": 1072753,
                    "translationId": 2380042,
                    "offset": -240,
                    "servingId": 5312075123,
                    "foodId": 464785,
                    "time": "03:58:00",
                    "grams": 210,
                    "type": "Serving",
                    "userId": 111222,
                    "day": "2026-10-16",
                    "order": 65537,
                }
            ],
            "summary": {
                "consumed": {
                    "kcal": 1850.5,
                    "protein_g": 140.0,
                    "carbs_g": 180.0,
                    "fat_g": 60.0,
                }
            },
            "info": {},
            "messages": [],
        }
        get_foods_resp = {
            "foods": [
                {
                    "id": 464785,
                    "name": "Greek Yogurt, Plain",
                    "measures": [{"id": 1072753, "name": "1 cup"}],
                }
            ]
        }

        with mock.patch.object(
            requests.Session,
            "request",
            side_effect=[
                make_mock_response(200, json_data=get_diary_resp),
                make_mock_response(200, json_data=get_foods_resp),
            ],
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            res = client.get_diary("2026-10-16")

            self.assertEqual(mock_req.call_count, 2)
            first_call = mock_req.call_args_list[0]
            self.assertEqual(first_call.kwargs["url"], "https://mobile.cronometer.com/api/v2/get_diary")
            self.assertEqual(first_call.kwargs["method"], "POST")
            self.assertEqual(first_call.kwargs["json"]["day"], "2026-10-16")
            self.assertEqual(first_call.kwargs["json"]["auth"]["userId"], 111222)
            self.assertEqual(first_call.kwargs["json"]["auth"]["token"], "sess_key_for_unit_tests_12345678")
            self.assertEqual(first_call.kwargs["json"]["lastSeen"], 0)

            second_call = mock_req.call_args_list[1]
            self.assertEqual(second_call.kwargs["url"], "https://mobile.cronometer.com/api/v2/get_foods")
            self.assertEqual(second_call.kwargs["json"]["ids"], [464785])
            self.assertEqual(second_call.kwargs["json"]["config"], {"call_version": 3})

        self.assertEqual(res["date"], "2026-10-16")
        self.assertEqual(res["totals"]["consumed"]["kcal"], 1850.5)
        self.assertEqual(res["totals"]["consumed"]["protein_g"], 140.0)
        self.assertEqual(res["totals"]["consumed"]["carbs_g"], 180.0)
        self.assertEqual(res["totals"]["consumed"]["fat_g"], 60.0)
        self.assertEqual(len(res["entries"]), 1)
        self.assertEqual(res["entries"][0]["servingId"], 5312075123)
        self.assertEqual(res["entries"][0]["foodId"], 464785)
        self.assertEqual(res["entries"][0]["grams"], 210)
        self.assertEqual(res["entries"][0]["name"], "Greek Yogurt, Plain")
        self.assertEqual(res["entries"][0]["type"], "Serving")

    def test_add_serving_payload_and_dry_run(self) -> None:
        payload = crono.build_diary_add_payload(
            food_id=464785,
            measure_id=1072753,
            amount=210.0,
            date_str="2026-10-18",
            time_str="08:30",
            user_id=111222,
            session_key="sess_key_for_unit_tests_12345678",
        )
        self.assertNotIn("servingId", payload["serving"])
        self.assertEqual(
            payload["serving"],
            {
                "foodId": 464785,
                "measureId": 1072753,
                "grams": 210.0,
                "day": "2026-10-18",
                "time": "08:30:00",
                "type": "Serving",
                "userId": 111222,
            },
        )
        self.assertEqual(payload["config"], {"call_version": 2})
        self.assertEqual(payload["auth"]["userId"], 111222)
        self.assertEqual(payload["lastSeen"], 0)

        # CLI missing --date or --time must fail argparse (never default to today/now)
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                crono.build_parser().parse_args(
                    ["diary", "add", "--food-id", "1", "--measure-id", "2", "--amount", "100", "--date", "2026-10-18"]
                )
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                crono.build_parser().parse_args(
                    ["diary", "add", "--food-id", "1", "--measure-id", "2", "--amount", "100", "--time", "09:00"]
                )

        # Dry run must not call requests.Session.request and must redact sessionKey
        with mock.patch.object(requests.Session, "request") as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            dry = client.add_diary_entry(
                food_id=464785,
                measure_id=1072753,
                amount=210.0,
                date_str="2026-10-18",
                time_str="08:30",
                dry_run=True,
            )
            mock_req.assert_not_called()
            self.assertTrue(dry["dryRun"])
            self.assertEqual(dry["endpoint"], "https://mobile.cronometer.com/api/v2/add_serving")
            self.assertEqual(dry["payload"]["serving"]["grams"], 210.0)
            self.assertEqual(dry["payload"]["serving"]["time"], "08:30:00")
            self.assertEqual(dry["payload"]["config"], {"call_version": 2})
            self.assertNotIn("sess_key_for_unit_tests_12345678", json.dumps(dry))

        # Live send posts exact payload to add_serving and extracts new servingId
        add_resp_data = {
            "servings": [{"servingId": 999888777, "foodId": 464785, "grams": 210.0}],
            "id": 999888777,
            "messages": [],
        }
        with mock.patch.object(
            requests.Session,
            "request",
            return_value=make_mock_response(200, json_data=add_resp_data),
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            sent = client.add_diary_entry(
                food_id=464785,
                measure_id=1072753,
                amount=210.0,
                date_str="2026-10-18",
                time_str="08:30",
                dry_run=False,
            )
            mock_req.assert_called_once()
            self.assertEqual(mock_req.call_args.kwargs["url"], "https://mobile.cronometer.com/api/v2/add_serving")
            self.assertEqual(mock_req.call_args.kwargs["json"], payload)
            self.assertEqual(sent["servingId"], 999888777)

    def test_delete_diary_entry_v3_flow_and_dry_run(self) -> None:
        entry_obj = {
            "measureId": 1072753,
            "translationId": 2380042,
            "offset": -240,
            "servingId": 5312075123,
            "foodId": 464785,
            "time": "03:58:00",
            "grams": 210,
            "type": "Serving",
            "userId": 111222,
            "day": "2026-10-18",
            "order": 65537,
        }
        diary_before = {"diary": [entry_obj], "summary": {}, "messages": []}
        diary_after = {"diary": [], "summary": {}, "messages": []}

        # Dry run fetches the day's diary to locate the entry object, then stops before DELETE
        with mock.patch.object(
            requests.Session,
            "request",
            return_value=make_mock_response(200, json_data=diary_before),
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            dry = client.delete_diary_entry(serving_id=5312075123, date_str="2026-10-18", dry_run=True)
            self.assertEqual(mock_req.call_count, 1)
            self.assertTrue(dry["dryRun"])
            self.assertEqual(dry["method"], "DELETE")
            self.assertEqual(dry["endpoint"], "https://mobile.cronometer.com/api/v3/user/111222/diary-entries")
            self.assertEqual(dry["payload"], {"diaryEntries": [entry_obj]})

        # Live delete: 1) V2 get_diary, 2) V3 DELETE 204, 3) V2 get_diary verification
        with mock.patch.object(
            requests.Session,
            "request",
            side_effect=[
                make_mock_response(200, json_data=diary_before),
                make_mock_response(204, text=""),
                make_mock_response(200, json_data=diary_after),
            ],
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            res = client.delete_diary_entry(serving_id=5312075123, date_str="2026-10-18", dry_run=False)
            self.assertEqual(mock_req.call_count, 3)

            del_call = mock_req.call_args_list[1]
            self.assertEqual(del_call.kwargs["method"], "DELETE")
            self.assertEqual(del_call.kwargs["url"], "https://mobile.cronometer.com/api/v3/user/111222/diary-entries")
            self.assertEqual(del_call.kwargs["json"], {"diaryEntries": [entry_obj]})
            self.assertEqual(del_call.kwargs["headers"]["X-CRONO-SESSION"], "sess_key_for_unit_tests_12345678")
            self.assertEqual(del_call.kwargs["headers"]["X-CRONO-APP-VERSION"], "4.58.3")
            self.assertEqual(del_call.kwargs["headers"]["X-CRONO-APP-BUILD-NUMBER"], "3541")
            self.assertEqual(del_call.kwargs["headers"]["X-CRONO-APP-OS"], "android")

            self.assertTrue(res["deleted"])
            self.assertEqual(res["beforeCount"], 1)
            self.assertEqual(res["afterCount"], 0)


class TestCronoFoodTargetsBiometricsErrors(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.tmpdir.name) / "session.json"
        crono.save_session_data(
            self.session_file,
            user_id=111222,
            session_key="sess_key_for_unit_tests_12345678",
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_v3_food_search_and_v2_custom_parsing(self) -> None:
        search_list = [
            {
                "name": "Banana, Fresh",
                "id": 450856,
                "score": 3634,
                "source": "NCCDB",
                "type": "FOOD",
                "category": 12,
                "measureId": 998940,
                "measureDisplayName": '1 medium - 7" to 7 7/8" long - 118g',
            }
        ]
        with mock.patch.object(
            requests.Session, "request", return_value=make_mock_response(200, json_data=search_list)
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            res = client.search_food("banana")
            mock_req.assert_called_once()
            self.assertEqual(mock_req.call_args.kwargs["method"], "GET")
            self.assertEqual(
                mock_req.call_args.kwargs["url"],
                "https://mobile.cronometer.com/api/v3/user/111222/food-search/string",
            )
            self.assertEqual(
                mock_req.call_args.kwargs["params"],
                {"query": "banana", "selectedTab": "ALL"},
            )
            self.assertEqual(
                mock_req.call_args.kwargs["headers"]["X-CRONO-SESSION"],
                "sess_key_for_unit_tests_12345678",
            )
            self.assertEqual(len(res["foods"]), 1)
            self.assertEqual(res["foods"][0]["id"], 450856)
            self.assertEqual(res["foods"][0]["name"], "Banana, Fresh")
            self.assertEqual(res["foods"][0]["source"], "NCCDB")
            self.assertEqual(res["foods"][0]["measureId"], 998940)
            self.assertEqual(res["foods"][0]["measureDisplayName"], '1 medium - 7" to 7 7/8" long - 118g')

        custom_json = {"foods": [{"id": 888, "name": "My Protein Shake", "type": "custom"}]}
        with mock.patch.object(
            requests.Session, "request", return_value=make_mock_response(200, json_data=custom_json)
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            custom_res = client.get_custom_foods()
            self.assertEqual(mock_req.call_args.kwargs["url"], "https://mobile.cronometer.com/api/v2/get_myfoods")
            self.assertEqual(len(custom_res["foods"]), 1)
            self.assertEqual(custom_res["foods"][0]["foodId"], 888)

    def test_v3_targets_parsing_from_list(self) -> None:
        targets_list = [
            {"id": 208, "min": 2000.0, "max": 2500.0, "vis": True, "custom": True},
            {"id": 203, "min": 150.0, "max": 220.0, "vis": True, "custom": False},
        ]
        with mock.patch.object(
            requests.Session, "request", return_value=make_mock_response(200, json_data=targets_list)
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            res = client.get_targets()
            mock_req.assert_called_once()
            self.assertEqual(mock_req.call_args.kwargs["method"], "GET")
            self.assertEqual(
                mock_req.call_args.kwargs["url"],
                "https://mobile.cronometer.com/api/v3/user/111222/targets",
            )
            self.assertEqual(res["count"], 2)
            self.assertEqual(res["targets"][0]["id"], 208)
            self.assertEqual(res["targets"][0]["min"], 2000.0)
            self.assertEqual(res["targets"][1]["id"], 203)

    def test_biometric_log_and_get_metrics_search(self) -> None:
        # Missing --date or --time in CLI must fail
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                crono.build_parser().parse_args(
                    ["biometric", "log", "--metric-id", "1", "--value", "75.5", "--date", "2026-10-15"]
                )

        payload = crono.build_biometric_log_payload(
            metric_id=1,
            value=75.4,
            date_str="2026-10-15",
            time_str="06:50",
            user_id=111222,
            session_key="sess_key_for_unit_tests_12345678",
        )
        self.assertEqual(payload["biometric"]["metricId"], 1)
        self.assertEqual(payload["biometric"]["amount"], 75.4)
        self.assertEqual(payload["biometric"]["day"], "2026-10-15")
        self.assertEqual(payload["biometric"]["time"], "06:50:00")
        self.assertEqual(payload["biometric"]["type"], "Biometric")
        self.assertEqual(payload["auth"]["userId"], 111222)

        with mock.patch.object(
            requests.Session,
            "request",
            return_value=make_mock_response(200, json_data={"id": 3210}),
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            res = client.log_biometric(metric_id=1, value=75.4, date_str="2026-10-15", time_str="06:50")
            mock_req.assert_called_once()
            self.assertEqual(mock_req.call_args.kwargs["url"], "https://mobile.cronometer.com/api/v2/add_biometric")
            self.assertEqual(mock_req.call_args.kwargs["json"], payload)
            self.assertEqual(res["entryId"], 3210)

        metrics_resp = {
            "metrics": [
                {"id": 1, "name": "Weight", "units": [{"id": 1, "name": "kg"}]},
                {"id": 2, "name": "Heart Rate (Resting)", "units": [{"id": 5, "name": "bpm"}]},
            ],
            "categoriesMap": {"10": {"id": 10, "name": "Body"}},
            "categoryMetricsMap": {"10": [{"id": 1, "name": "Weight"}]},
        }
        with mock.patch.object(
            requests.Session, "request", return_value=make_mock_response(200, json_data=metrics_resp)
        ) as mock_req:
            client = crono.CronoClient(session_file=str(self.session_file))
            sres = client.search_biometrics("Weight")
            self.assertEqual(mock_req.call_args.kwargs["url"], "https://mobile.cronometer.com/api/v2/get_metrics")
            self.assertEqual(mock_req.call_args.kwargs["json"]["includeForest"], True)
            self.assertEqual(len(sres["metrics"]), 1)
            self.assertEqual(sres["metrics"][0]["metricId"], 1)
            self.assertEqual(sres["metrics"][0]["unit"], "kg")
            self.assertEqual(sres["metrics"][0]["category"], "Body")

    def test_error_envelopes_401_403_and_non_json(self) -> None:
        # Top-level {"error": "..."} in HTTP 200 JSON -> CronoError
        with mock.patch.object(
            requests.Session,
            "request",
            return_value=make_mock_response(200, json_data={"error": "TOTP_CODE_REQUIRED"}),
        ):
            client = crono.CronoClient(session_file=str(self.session_file))
            with self.assertRaises(crono.CronoError) as ctx_err:
                client.login("u@example.com", "pw")
            self.assertIn("TOTP_CODE_REQUIRED", str(ctx_err.exception))

        # Legacy {"result": "FAIL", "error": "..."} in HTTP 200 JSON -> CronoError
        with mock.patch.object(
            requests.Session,
            "request",
            return_value=make_mock_response(200, json_data={"result": "FAIL", "error": "Invalid request"}),
        ):
            client = crono.CronoClient(session_file=str(self.session_file))
            with self.assertRaises(crono.CronoError) as ctx_fail:
                client.get_diary("2026-10-18")
            self.assertIn("Invalid request", str(ctx_fail.exception))

        # HTTP 401 -> CronoError
        with mock.patch.object(
            requests.Session, "request", return_value=make_mock_response(401, text="Unauthorized")
        ):
            client = crono.CronoClient(session_file=str(self.session_file))
            with self.assertRaises(crono.CronoError) as ctx:
                client.get_targets()
            self.assertIn("session missing or expired, run auth login", str(ctx.exception))

        # Non-JSON 200 response -> CronoError
        long_html = "<html><body>" + ("X" * 300) + "</body></html>"
        with mock.patch.object(
            requests.Session, "request", return_value=make_mock_response(200, text=long_html)
        ):
            client = crono.CronoClient(session_file=str(self.session_file))
            with self.assertRaises(crono.CronoError) as ctx2:
                client.get_targets()
            msg = str(ctx2.exception)
            self.assertIn("Non-JSON response (HTTP 200):", msg)
            self.assertIn("<html><body>", msg)
            self.assertLessEqual(len(msg), len("Non-JSON response (HTTP 200): ") + 200)


class TestCronoDurability(unittest.TestCase):
    """Self-healing: rotation persistence, credentials file, auto-relogin."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.session_file = Path(self.tmpdir.name) / "session.json"
        self.creds_file = Path(self.tmpdir.name) / "credentials"
        crono.save_session_data(
            self.session_file,
            user_id=111222,
            session_key="dead_key_for_durability_tests_0",
        )
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

    def _write_creds(self, mode: int = 0o600, body: str = "email=u@example.com\npassword=file_pw\n") -> None:
        self.creds_file.write_text(body, encoding="utf-8")
        os.chmod(self.creds_file, mode)

    def test_is_auth_failure_markers(self) -> None:
        self.assertTrue(crono.is_auth_failure(crono.CronoError("API error: Token Authorization failed")))
        self.assertTrue(crono.is_auth_failure(crono.CronoError("API error: Authentication Failed")))
        self.assertTrue(
            crono.is_auth_failure(crono.CronoError("HTTP 401: session missing or expired, run auth login"))
        )
        self.assertFalse(crono.is_auth_failure(crono.CronoError("API error: Invalid request")))
        self.assertFalse(crono.is_auth_failure(crono.CronoError("API error: TOTP_CODE_REQUIRED")))

    def test_check_status_persists_rotated_key(self) -> None:
        resume_resp = make_mock_response(
            status_code=200,
            json_data={"id": 111222, "sessionKey": "rotated_key_from_resume_00000000", "serviceLevel": "GOLD"},
        )
        with mock.patch.object(requests.Session, "request", return_value=resume_resp):
            client = crono.CronoClient(session_file=str(self.session_file))
            status = client.check_status()

        self.assertTrue(status["loggedIn"])
        self.assertTrue(status["rotated"])
        self.assertEqual(client.session_key, "rotated_key_from_resume_00000000")
        saved = crono.load_session_data(self.session_file)
        self.assertEqual(saved["sessionKey"], "rotated_key_from_resume_00000000")
        self.assertIn("lastLoginAt", saved)

    def test_check_status_no_rotation_same_key(self) -> None:
        resume_resp = make_mock_response(
            status_code=200,
            json_data={"id": 111222, "sessionKey": "dead_key_for_durability_tests_0", "serviceLevel": "GOLD"},
        )
        with mock.patch.object(requests.Session, "request", return_value=resume_resp):
            client = crono.CronoClient(session_file=str(self.session_file))
            status = client.check_status()

        self.assertTrue(status["loggedIn"])
        self.assertFalse(status["rotated"])
        saved = crono.load_session_data(self.session_file)
        self.assertEqual(saved["sessionKey"], "dead_key_for_durability_tests_0")

    def test_credentials_file_parsing_and_perms(self) -> None:
        self.assertIsNone(crono.load_credentials_file())  # missing file
        self._write_creds(body="# comment\n\nemail=u@example.com\npassword=file_pw\n")
        self.assertEqual(crono.load_credentials_file(), ("u@example.com", "file_pw"))
        self._write_creds(body="email=u@example.com\npassword=\n")
        self.assertIsNone(crono.load_credentials_file())  # empty password
        self._write_creds(mode=0o644)
        with self.assertRaises(crono.CronoError):
            crono.load_credentials_file()

    def test_stored_credentials_env_precedence(self) -> None:
        self._write_creds()
        self.assertEqual(crono.stored_credentials(), ("u@example.com", "file_pw"))
        os.environ["CRONOMETER_USER"] = "env@example.com"
        os.environ["CRONOMETER_PASS"] = "env_pw"
        self.assertEqual(crono.stored_credentials(), ("env@example.com", "env_pw"))

    def test_main_retries_once_after_autologin(self) -> None:
        os.environ["CRONOMETER_USER"] = "u@example.com"
        os.environ["CRONOMETER_PASS"] = "env_pw"
        calls = {"diary": 0, "login": 0}

        def fake_request(**kwargs: object) -> mock.MagicMock:
            url = str(kwargs.get("url", ""))
            if url.endswith("/login"):
                calls["login"] += 1
                return make_mock_response(
                    200,
                    json_data={"id": 111222, "sessionKey": "fresh_key_after_relogin_000000", "serviceLevel": "GOLD"},
                )
            if url.endswith("/get_diary"):
                calls["diary"] += 1
                if calls["diary"] == 1:
                    return make_mock_response(200, json_data={"error": "Token Authorization failed"})
                return make_mock_response(
                    200,
                    json_data={
                        "diary": [],
                        "summary": {"consumed": {"kcal": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}},
                        "info": {},
                        "messages": [],
                    },
                )
            if url.endswith("/get_foods"):
                return make_mock_response(200, json_data={"foods": []})
            raise AssertionError(f"unexpected URL {url}")

        with mock.patch.object(requests.Session, "request", side_effect=fake_request):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = crono.main(
                    ["--session-file", str(self.session_file), "diary", "show", "--date", "2026-10-16"]
                )

        self.assertEqual(rc, 0)
        self.assertEqual(calls["diary"], 2)
        self.assertEqual(calls["login"], 1)
        saved = crono.load_session_data(self.session_file)
        self.assertEqual(saved["sessionKey"], "fresh_key_after_relogin_000000")

    def test_main_without_credentials_does_not_retry(self) -> None:
        calls = {"diary": 0}

        def fake_request(**kwargs: object) -> mock.MagicMock:
            url = str(kwargs.get("url", ""))
            if url.endswith("/get_diary"):
                calls["diary"] += 1
                return make_mock_response(200, json_data={"error": "Token Authorization failed"})
            raise AssertionError(f"unexpected URL {url}")

        with mock.patch.object(requests.Session, "request", side_effect=fake_request):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = crono.main(
                    ["--session-file", str(self.session_file), "diary", "show", "--date", "2026-10-16"]
                )

        self.assertEqual(rc, 1)
        self.assertEqual(calls["diary"], 1)

    def test_auth_status_heals_via_main(self) -> None:
        os.environ["CRONOMETER_USER"] = "u@example.com"
        os.environ["CRONOMETER_PASS"] = "env_pw"
        calls = {"resume": 0}

        def fake_request(**kwargs: object) -> mock.MagicMock:
            url = str(kwargs.get("url", ""))
            body = kwargs.get("json") or {}
            if url.endswith("/login") and "email" in body:
                return make_mock_response(
                    200,
                    json_data={"id": 111222, "sessionKey": "healed_key_from_status_00000000", "serviceLevel": "GOLD"},
                )
            if url.endswith("/login"):
                calls["resume"] += 1
                if calls["resume"] == 1:
                    return make_mock_response(200, json_data={"error": "Token Authorization failed"})
                return make_mock_response(
                    200,
                    json_data={"id": 111222, "sessionKey": "healed_key_from_status_00000000", "serviceLevel": "GOLD"},
                )
            raise AssertionError(f"unexpected URL {url}")

        out = io.StringIO()
        with mock.patch.object(requests.Session, "request", side_effect=fake_request):
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                rc = crono.main(["--session-file", str(self.session_file), "auth", "status"])

        self.assertEqual(rc, 0)
        self.assertIn("logged_in: true", out.getvalue())
        self.assertIn("healed: true", out.getvalue())
        saved = crono.load_session_data(self.session_file)
        self.assertEqual(saved["sessionKey"], "healed_key_from_status_00000000")


if __name__ == "__main__":
    unittest.main()
