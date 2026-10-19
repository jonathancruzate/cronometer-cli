#!/usr/bin/env python3
"""crono — Cronometer CLI talking to Cronometer's verified mobile V2 RPC & V3 REST APIs."""

from __future__ import annotations

import argparse
import datetime
import getpass
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests

DEFAULT_BASE_URL = "https://mobile.cronometer.com/api/v2/"
DEFAULT_V3_BASE_URL = "https://mobile.cronometer.com/api/v3/"
DEFAULT_WEB_BASE_URL = "https://cronometer.com"
DEFAULT_SESSION_FILE = "~/.config/cronometer-cli/session.json"

APP_VERSION = "4.58.3"
APP_BUILD_NUMBER = "3541"
APP_BUILD_STRING = "4.58.3 b3541-a"
APP_DEVICE_STRING = "Android 14 (SDK 34), Google Pixel 8"
APP_TIMEZONE = "America/New_York"


class CronoError(Exception):
    """Application-level CLI error."""


def resolve_session_path(session_file: Optional[str] = None) -> Path:
    path_str = session_file or os.environ.get("CRONOMETER_SESSION_FILE") or DEFAULT_SESSION_FILE
    return Path(path_str).expanduser()


def load_session_data(session_path: Path) -> Dict[str, Any]:
    if not session_path.exists():
        return {}
    try:
        with session_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise CronoError(f"Failed to read session file {session_path}: {exc}") from exc


def save_session_data(
    session_path: Path,
    user_id: Optional[int] = None,
    session_key: Optional[str] = None,
    cookies: Optional[Dict[str, str]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    session_path.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if session_path.exists():
        try:
            with session_path.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}

    payload: Dict[str, Any] = dict(existing)
    if user_id is not None:
        payload["userId"] = int(user_id)
    if session_key is not None:
        payload["sessionKey"] = str(session_key)
    if cookies is not None:
        payload["cookies"] = dict(cookies)
    payload["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if meta:
        payload.update(meta)

    fd = os.open(str(session_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
    finally:
        try:
            os.chmod(session_path, 0o600)
        except OSError:
            pass


def extract_anticsrf(html_text: str) -> str:
    if not html_text:
        return ""
    patterns = [
        r'<input[^>]*name=["\']anticsrf["\'][^>]*value=["\']([^"\']*)["\']',
        r'<input[^>]*value=["\']([^"\']*)["\'][^>]*name=["\']anticsrf["\']',
        r'anticsrf\s*[:=]\s*["\']([^"\']+)["\']',
    ]
    for pat in patterns:
        match = re.search(pat, html_text, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def validate_date(date_str: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str):
        raise CronoError(f"Invalid date format '{date_str}'; expected YYYY-MM-DD")
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError as exc:
        raise CronoError(f"Invalid calendar date '{date_str}': {exc}") from exc
    return date_str


def validate_time(time_str: str) -> str:
    if not re.fullmatch(r"\d{2}:\d{2}(:\d{2})?", time_str):
        raise CronoError(f"Invalid time format '{time_str}'; expected HH:MM or HH:MM:SS")
    parts = [int(part) for part in time_str.split(":")]
    hh, mm = parts[0], parts[1]
    ss = parts[2] if len(parts) == 3 else 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        raise CronoError(f"Invalid clock time '{time_str}'; hours must be 00-23 and minutes/seconds 00-59")
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def build_auth_envelope(user_id: Optional[int] = None, session_key: Optional[str] = None) -> Dict[str, Any]:
    return {
        "userId": int(user_id) if user_id is not None else None,
        "token": str(session_key) if session_key is not None else None,
        "api": 3,
        "os": "Android",
        "build": APP_BUILD_NUMBER,
        "flavour": None,
    }


def build_serving_object(
    food_id: int,
    measure_id: int,
    amount: float,
    date_str: str,
    time_str: str,
    user_id: Optional[int] = None,
    group_id: Optional[int] = None,
    order: Optional[int] = None,
) -> Dict[str, Any]:
    day = validate_date(date_str)
    tm = validate_time(time_str)
    serving: Dict[str, Any] = {
        "foodId": int(food_id),
        "measureId": int(measure_id),
        "grams": float(amount),
        "day": day,
        "time": tm,
        "type": "Serving",
        "userId": int(user_id) if user_id is not None else None,
    }
    if order is not None:
        serving["order"] = int(order)
    elif group_id is not None:
        serving["order"] = int(group_id)
    if group_id is not None:
        serving["groupId"] = int(group_id)
    return serving


def build_diary_add_payload(
    food_id: int,
    measure_id: int,
    amount: float,
    date_str: str,
    time_str: str,
    user_id: Optional[int] = None,
    session_key: Optional[str] = None,
    group_id: Optional[int] = None,
    order: Optional[int] = None,
) -> Dict[str, Any]:
    serving = build_serving_object(
        food_id=food_id,
        measure_id=measure_id,
        amount=amount,
        date_str=date_str,
        time_str=time_str,
        user_id=user_id,
        group_id=group_id,
        order=order,
    )
    return {
        "serving": serving,
        "auth": build_auth_envelope(user_id, session_key),
        "lastSeen": 0,
        "config": {"call_version": 2},
    }


def build_biometric_object(
    metric_id: int,
    value: float,
    date_str: str,
    time_str: str,
    user_id: Optional[int] = None,
    unit_id: Optional[int] = None,
    order: Optional[int] = None,
) -> Dict[str, Any]:
    day = validate_date(date_str)
    tm = validate_time(time_str)
    bio: Dict[str, Any] = {
        "metricId": int(metric_id),
        "unitId": int(unit_id) if unit_id is not None else 1,
        "amount": float(value),
        "value": float(value),
        "day": day,
        "time": tm,
        "type": "Biometric",
        "userId": int(user_id) if user_id is not None else None,
        "order": int(order) if order is not None else 65537,
    }
    return bio


def build_biometric_log_payload(
    metric_id: int,
    value: float,
    date_str: str,
    time_str: str,
    user_id: Optional[int] = None,
    session_key: Optional[str] = None,
    unit_id: Optional[int] = None,
    order: Optional[int] = None,
) -> Dict[str, Any]:
    bio = build_biometric_object(
        metric_id=metric_id,
        value=value,
        date_str=date_str,
        time_str=time_str,
        user_id=user_id,
        unit_id=unit_id,
        order=order,
    )
    return {
        "biometric": bio,
        "auth": build_auth_envelope(user_id, session_key),
        "lastSeen": 0,
    }


class CronoClient:
    """HTTP client wrapper for Cronometer private mobile V2 RPC, V3 REST, and web APIs."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        web_base_url: str = DEFAULT_WEB_BASE_URL,
        session_file: Optional[str] = None,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.base_url = base_url if base_url.endswith("/") else f"{base_url}/"
        if "/api/v2/" in self.base_url:
            self.v3_base_url = self.base_url.replace("/api/v2/", "/api/v3/")
        else:
            self.v3_base_url = DEFAULT_V3_BASE_URL
        self.web_base_url = web_base_url.rstrip("/")
        self.session_path = resolve_session_path(session_file)
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", "Dart/3.11 (dart:io)")
        self.session.headers.setdefault("Accept", "application/json, text/plain, */*")
        self.user_id: Optional[int] = None
        self.session_key: Optional[str] = None
        self._load_session()

    def _load_session(self) -> None:
        data = load_session_data(self.session_path)
        uid = data.get("userId")
        if uid is not None:
            try:
                self.user_id = int(uid)
            except (TypeError, ValueError):
                self.user_id = None
        skey = data.get("sessionKey")
        if isinstance(skey, str) and skey:
            self.session_key = skey
        cookies = data.get("cookies")
        if isinstance(cookies, dict):
            for k, v in cookies.items():
                self.session.cookies.set(str(k), str(v))

    def _endpoint(self, path: str, v3: bool = False) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        clean = path.lstrip("/")
        base = self.v3_base_url if v3 else self.base_url
        if not v3 and clean.startswith("api/v2/") and base.rstrip("/").endswith("/api/v2"):
            clean = clean[len("api/v2/"):]
        if v3 and clean.startswith("api/v3/") and base.rstrip("/").endswith("/api/v3"):
            clean = clean[len("api/v3/"):]
        return urljoin(base, clean)

    def _auth_envelope(self, user_id: Optional[int] = None, session_key: Optional[str] = None) -> Dict[str, Any]:
        uid = user_id if user_id is not None else self.user_id
        tok = session_key if session_key is not None else self.session_key
        return build_auth_envelope(uid, tok)

    def _v3_headers(self, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        hdrs: Dict[str, str] = {
            "X-CRONO-SESSION": self.session_key or "",
            "X-CRONO-APP-VERSION": APP_VERSION,
            "X-CRONO-APP-BUILD-NUMBER": APP_BUILD_NUMBER,
            "X-CRONO-APP-OS": "android",
            "Content-Type": "application/json",
        }
        if extra_headers:
            hdrs.update(extra_headers)
        return hdrs

    def _redact_secrets(self, text: str) -> str:
        if not text:
            return text
        if self.session_key and len(self.session_key) >= 4:
            text = text.replace(self.session_key, "[REDACTED]")
        return text

    def _handle_response(self, resp: requests.Response) -> Any:
        if resp.status_code in (401, 403):
            raise CronoError(f"HTTP {resp.status_code}: session missing or expired, run auth login")
        text = self._redact_secrets(resp.text or "")
        if resp.status_code >= 400:
            snippet = text[:200].replace("\n", " ")
            raise CronoError(f"HTTP {resp.status_code}: {snippet}")
        if resp.status_code == 204 or not text.strip():
            return {}
        try:
            parsed = resp.json()
        except ValueError as exc:
            snippet = text[:200]
            raise CronoError(f"Non-JSON response (HTTP {resp.status_code}): {snippet}") from exc

        if isinstance(parsed, dict):
            if parsed.get("result") == "FAIL":
                err_msg = self._redact_secrets(str(parsed.get("error", "unknown error")))
                raise CronoError(f"API error: {err_msg}")
            if parsed.get("error") is not None and parsed.get("error") != "":
                err_msg = self._redact_secrets(str(parsed.get("error")))
                raise CronoError(f"API error: {err_msg}")
        return parsed

    def post_v2(
        self,
        endpoint: str,
        body: Optional[Dict[str, Any]] = None,
        call_version: Optional[int] = None,
        include_auth: bool = True,
    ) -> Any:
        payload: Dict[str, Any] = dict(body or {})
        if include_auth:
            if "auth" not in payload:
                payload["auth"] = self._auth_envelope()
            if "lastSeen" not in payload:
                payload["lastSeen"] = 0
        if call_version is not None and "config" not in payload:
            payload["config"] = {"call_version": int(call_version)}

        url = self._endpoint(endpoint, v3=False)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        resp = self.session.request(
            method="POST",
            url=url,
            json=payload,
            headers=headers,
            timeout=20,
        )
        return self._handle_response(resp)

    def request_v3(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        url = self._endpoint(path, v3=True)
        hdrs = self._v3_headers(headers)
        resp = self.session.request(
            method=method.upper(),
            url=url,
            params=params,
            json=json_body,
            headers=hdrs,
            timeout=20,
        )
        return self._handle_response(resp)

    def login(self, email: str, password: str, totp: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "email": email,
            "password": password,
            "timezone": APP_TIMEZONE,
            "userCode": totp if totp else None,
            "build": APP_BUILD_STRING,
            "device": APP_DEVICE_STRING,
            "firebaseToken": None,
            "auth": build_auth_envelope(None, None),
            "lastSeen": 0,
            "config": {"call_version": 4},
        }
        resp = self.post_v2("login", body=payload, include_auth=False)
        if not isinstance(resp, dict):
            raise CronoError("Login failed: unexpected non-object JSON response")

        user_id = resp.get("id")
        session_key = resp.get("sessionKey")
        if user_id is None or not session_key:
            raise CronoError("Login failed: response missing id or sessionKey")

        self.user_id = int(user_id)
        self.session_key = str(session_key)
        save_session_data(
            self.session_path,
            user_id=self.user_id,
            session_key=self.session_key,
        )
        return {
            "success": True,
            "userId": self.user_id,
            "serviceLevel": resp.get("serviceLevel"),
            "sessionFile": str(self.session_path),
        }

    def web_login(self, username: str, password: str, totp: Optional[str] = None) -> Dict[str, Any]:
        login_page_url = f"{self.web_base_url}/login/"
        login_post_url = f"{self.web_base_url}/login"

        page_resp = self.session.get(login_page_url, timeout=20)
        if page_resp.status_code in (401, 403):
            raise CronoError(f"HTTP {page_resp.status_code}: session missing or expired, run auth login")
        anticsrf = extract_anticsrf(page_resp.text or "")

        form_data: Dict[str, str] = {
            "username": username,
            "password": password,
            "anticsrf": anticsrf,
            "userCode": totp or "",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        post_resp = self.session.post(
            login_post_url,
            data=form_data,
            headers=headers,
            timeout=20,
            allow_redirects=False,
        )
        if post_resp.status_code in (301, 302, 303, 307, 308):
            cookies_dict = self.session.cookies.get_dict()
            if cookies_dict:
                save_session_data(self.session_path, cookies=cookies_dict)
                return {
                    "success": True,
                    "sessionFile": str(self.session_path),
                    "cookieNames": sorted(cookies_dict.keys()),
                    "redirect": post_resp.headers.get("Location", ""),
                }
        if post_resp.status_code in (401, 403):
            raise CronoError(f"HTTP {post_resp.status_code}: session missing or expired, run auth login")
        if post_resp.status_code >= 400:
            snippet = (post_resp.text or "")[:200]
            raise CronoError(f"Login failed (HTTP {post_resp.status_code}): {snippet}")

        body_data: Dict[str, Any] = {}
        if (post_resp.text or "").strip():
            try:
                parsed = post_resp.json()
                if isinstance(parsed, dict):
                    body_data = parsed
            except ValueError:
                body_data = {}

        if body_data.get("error"):
            raise CronoError(f"Login failed: {body_data.get('error')}")
        if body_data.get("success") is False:
            raise CronoError("Login failed: server returned success=false")

        cookies_dict = self.session.cookies.get_dict()
        if hasattr(self.session.cookies, "items") and not cookies_dict:
            cookies_dict = dict(self.session.cookies.items())

        if not cookies_dict and not body_data.get("success"):
            snippet = (post_resp.text or "")[:200]
            raise CronoError(f"Login did not yield session cookies (HTTP {post_resp.status_code}): {snippet}")

        save_session_data(self.session_path, cookies=cookies_dict)
        return {
            "success": True,
            "sessionFile": str(self.session_path),
            "cookieNames": sorted(cookies_dict.keys()),
        }

    def check_status(self) -> Dict[str, Any]:
        if self.user_id is None or not self.session_key:
            return {
                "loggedIn": False,
                "userId": self.user_id,
                "sessionFile": str(self.session_path),
                "error": "No saved userId/sessionKey in session file",
            }

        payload: Dict[str, Any] = {
            "timezone": APP_TIMEZONE,
            "device": APP_DEVICE_STRING,
            "build": APP_BUILD_STRING,
            "firebaseToken": None,
            "auth": self._auth_envelope(),
            "lastSeen": 0,
            "config": {"call_version": 4},
        }
        try:
            resp = self.post_v2("login", body=payload, include_auth=False)
            logged_in = isinstance(resp, dict) and (resp.get("id") is not None or resp.get("sessionKey") is not None)
            uid = int(resp["id"]) if isinstance(resp, dict) and resp.get("id") is not None else self.user_id
            return {
                "loggedIn": logged_in,
                "userId": uid,
                "serviceLevel": resp.get("serviceLevel") if isinstance(resp, dict) else None,
                "sessionFile": str(self.session_path),
            }
        except (CronoError, requests.RequestException) as exc:
            return {
                "loggedIn": False,
                "userId": self.user_id,
                "sessionFile": str(self.session_path),
                "error": self._redact_secrets(str(exc)),
            }

    def get_foods(self, food_ids: List[int]) -> Dict[int, Dict[str, Any]]:
        unique_ids = []
        seen = set()
        for fid in food_ids:
            if fid is not None and int(fid) not in seen:
                seen.add(int(fid))
                unique_ids.append(int(fid))
        if not unique_ids:
            return {}

        foods_by_id: Dict[int, Dict[str, Any]] = {}
        # Batch up to 25 IDs per call per API::getFoods
        for i in range(0, len(unique_ids), 25):
            batch = unique_ids[i : i + 25]
            resp = self.post_v2("get_foods", body={"ids": batch}, call_version=3)
            if isinstance(resp, dict) and isinstance(resp.get("foods"), list):
                for f in resp["foods"]:
                    if isinstance(f, dict) and f.get("id") is not None:
                        foods_by_id[int(f["id"])] = f
                    elif isinstance(f, dict) and f.get("foodId") is not None:
                        foods_by_id[int(f["foodId"])] = f
        return foods_by_id

    def get_diary(self, date_str: str, resolve_foods: bool = True) -> Dict[str, Any]:
        day = validate_date(date_str)
        raw_diary = self.post_v2("get_diary", body={"day": day})
        foods_map: Dict[int, Dict[str, Any]] = {}
        if resolve_foods and isinstance(raw_diary, dict) and isinstance(raw_diary.get("diary"), list):
            missing_food_ids = [
                int(entry["foodId"])
                for entry in raw_diary["diary"]
                if isinstance(entry, dict)
                and entry.get("foodId") is not None
                and not (entry.get("name") or entry.get("foodName") or entry.get("description"))
            ]
            if missing_food_ids:
                try:
                    foods_map = self.get_foods(missing_food_ids)
                except (CronoError, requests.RequestException):
                    foods_map = {}
        return parse_diary_response(day, raw_diary, foods_map=foods_map)

    def add_diary_entry(
        self,
        food_id: int,
        measure_id: int,
        amount: float,
        date_str: str,
        time_str: str,
        group_id: Optional[int] = None,
        order: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        payload = build_diary_add_payload(
            food_id=food_id,
            measure_id=measure_id,
            amount=amount,
            date_str=date_str,
            time_str=time_str,
            user_id=self.user_id,
            session_key=self.session_key,
            group_id=group_id,
            order=order,
        )
        if dry_run:
            # Redact token in dry-run output while preserving full structure
            dry_payload = json.loads(json.dumps(payload))
            if isinstance(dry_payload.get("auth"), dict) and dry_payload["auth"].get("token"):
                dry_payload["auth"]["token"] = "<sessionKey>"
            return {
                "dryRun": True,
                "method": "POST",
                "endpoint": self._endpoint("add_serving", v3=False),
                "payload": dry_payload,
            }
        resp = self.post_v2("add_serving", body=payload, include_auth=False)
        serving_id = _extract_serving_id(resp)
        return {
            "dryRun": False,
            "servingId": serving_id,
            "entryId": serving_id,
            "payload": payload["serving"],
            "response": resp,
        }

    def delete_diary_entry(
        self,
        serving_id: int,
        date_str: str,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        day = validate_date(date_str)
        target_id = int(serving_id)
        if self.user_id is None:
            raise CronoError("Cannot delete diary entry: userId is not set (run auth login)")

        raw_before = self.post_v2("get_diary", body={"day": day})
        diary_list = raw_before.get("diary") if isinstance(raw_before, dict) else []
        if not isinstance(diary_list, list):
            diary_list = []

        matched_entry: Optional[Dict[str, Any]] = None
        for entry in diary_list:
            if not isinstance(entry, dict):
                continue
            eid = entry.get("servingId") or entry.get("biometricId") or entry.get("exerciseId") or entry.get("noteId") or entry.get("id")
            if eid is not None and int(eid) == target_id:
                matched_entry = entry
                break

        if matched_entry is None:
            raise CronoError(f"Serving ID {target_id} not found in diary for {day}")

        v3_path = f"user/{self.user_id}/diary-entries"
        delete_body = {"diaryEntries": [matched_entry]}
        if dry_run:
            return {
                "dryRun": True,
                "method": "DELETE",
                "endpoint": self._endpoint(v3_path, v3=True),
                "servingId": target_id,
                "date": day,
                "payload": delete_body,
            }

        self.request_v3("DELETE", v3_path, json_body=delete_body)
        raw_after = self.post_v2("get_diary", body={"day": day})
        after_list = raw_after.get("diary") if isinstance(raw_after, dict) and isinstance(raw_after.get("diary"), list) else []
        still_present = any(
            isinstance(e, dict)
            and (e.get("servingId") or e.get("biometricId") or e.get("id")) == target_id
            for e in after_list
        )
        return {
            "dryRun": False,
            "servingId": target_id,
            "entryId": target_id,
            "date": day,
            "deleted": not still_present,
            "beforeCount": len(diary_list),
            "afterCount": len(after_list),
            "deletedEntry": matched_entry,
        }

    def search_food(self, query: str) -> Dict[str, Any]:
        if self.user_id is None:
            raise CronoError("Cannot search foods: userId is not set (run auth login)")
        path = f"user/{self.user_id}/food-search/string"
        raw = self.request_v3("GET", path, params={"query": query, "selectedTab": "ALL"})
        return parse_food_search_response(query, raw)

    def get_custom_foods(self) -> Dict[str, Any]:
        raw = self.post_v2("get_myfoods", body={}, call_version=2)
        return parse_custom_foods_response(raw)

    def get_targets(self) -> Dict[str, Any]:
        if self.user_id is None:
            raise CronoError("Cannot fetch targets: userId is not set (run auth login)")
        path = f"user/{self.user_id}/targets"
        raw = self.request_v3("GET", path)
        return parse_targets_response(raw)

    def log_biometric(
        self,
        metric_id: int,
        value: float,
        date_str: str,
        time_str: str,
        unit_id: Optional[int] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        payload = build_biometric_log_payload(
            metric_id=metric_id,
            value=value,
            date_str=date_str,
            time_str=time_str,
            user_id=self.user_id,
            session_key=self.session_key,
            unit_id=unit_id,
        )
        if dry_run:
            dry_payload = json.loads(json.dumps(payload))
            if isinstance(dry_payload.get("auth"), dict) and dry_payload["auth"].get("token"):
                dry_payload["auth"]["token"] = "<sessionKey>"
            return {
                "dryRun": True,
                "method": "POST",
                "endpoint": self._endpoint("add_biometric", v3=False),
                "payload": dry_payload,
            }
        resp = self.post_v2("add_biometric", body=payload, include_auth=False)
        entry_id = _extract_serving_id(resp)
        return {
            "dryRun": False,
            "entryId": entry_id,
            "payload": payload["biometric"],
            "response": resp,
        }

    def search_biometrics(self, query: Optional[str] = None) -> Dict[str, Any]:
        raw = self.post_v2("get_metrics", body={"includeForest": True})
        return parse_biometric_search_response(query, raw)


def _extract_serving_id(resp: Any) -> Optional[Any]:
    if isinstance(resp, dict):
        for key in ("id", "servingId", "biometricId", "entryId", "diaryEntryId"):
            if key in resp and resp[key] is not None:
                return resp[key]
        if isinstance(resp.get("servings"), list) and resp["servings"]:
            first = resp["servings"][0]
            if isinstance(first, dict) and first.get("servingId") is not None:
                return first["servingId"]
        if isinstance(resp.get("biometric"), dict):
            return _extract_serving_id(resp["biometric"])
        if isinstance(resp.get("entry"), dict):
            return _extract_serving_id(resp["entry"])
    return None


def _normalize_entry(item: Dict[str, Any], foods_map: Optional[Dict[int, Dict[str, Any]]] = None) -> Dict[str, Any]:
    food_id = item.get("foodId")
    measure_id = item.get("measureId")
    serving_id = (
        item.get("servingId")
        or item.get("biometricId")
        or item.get("exerciseId")
        or item.get("noteId")
        or item.get("id")
        or item.get("entryId")
    )
    name = item.get("name") or item.get("foodName") or item.get("description")
    measure_name = item.get("measure") or item.get("measureName") or item.get("servingName") or item.get("unit")

    if foods_map and food_id is not None and int(food_id) in foods_map:
        f_obj = foods_map[int(food_id)]
        if not name:
            name = f_obj.get("name") or f_obj.get("description")
        if not measure_name and measure_id is not None:
            measures = f_obj.get("measures") or f_obj.get("servings") or []
            if isinstance(measures, list):
                for m in measures:
                    if isinstance(m, dict) and (m.get("id") == measure_id or m.get("measureId") == measure_id):
                        measure_name = m.get("name") or m.get("measureName")
                        break

    grams = item.get("grams")
    amount = grams if grams is not None else (item.get("amount") if item.get("amount") is not None else item.get("value"))

    return {
        "id": serving_id,
        "servingId": item.get("servingId") or serving_id,
        "type": item.get("type") or "Serving",
        "time": item.get("time") or item.get("timestamp") or "",
        "name": name,
        "foodId": food_id,
        "metricId": item.get("metricId"),
        "grams": grams,
        "amount": amount,
        "measureId": measure_id,
        "measure": measure_name or measure_id,
        "day": item.get("day"),
        "order": item.get("order"),
    }


def parse_diary_response(
    day: str,
    raw_diary: Any,
    foods_map: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    entries_list: List[Dict[str, Any]] = []
    raw_entries: List[Any] = []

    if isinstance(raw_diary, dict):
        if isinstance(raw_diary.get("diary"), list):
            raw_entries = raw_diary["diary"]
        elif isinstance(raw_diary.get("servings"), list):
            raw_entries = raw_diary["servings"]
        elif isinstance(raw_diary.get("entries"), list):
            raw_entries = raw_diary["entries"]
    elif isinstance(raw_diary, list):
        raw_entries = raw_diary

    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        entries_list.append(_normalize_entry(item, foods_map=foods_map))

    summary: Dict[str, Any] = {}
    consumed: Dict[str, Any] = {}
    totals: Dict[str, Any] = {}

    if isinstance(raw_diary, dict):
        raw_summary = raw_diary.get("summary")
        if isinstance(raw_summary, dict):
            summary = dict(raw_summary)
            raw_consumed = raw_summary.get("consumed")
            if isinstance(raw_consumed, dict):
                consumed = dict(raw_consumed)

        # Extract standard consumed macro totals (kcal, protein_g, carbs_g, fat_g)
        kcal = (
            consumed.get("kcal")
            if consumed.get("kcal") is not None
            else (
                consumed.get("calories")
                if consumed.get("calories") is not None
                else (
                    consumed.get("energy")
                    if consumed.get("energy") is not None
                    else (
                        consumed.get("total")
                        if consumed.get("total") is not None
                        else consumed.get("208")
                    )
                )
            )
        )
        protein_g = (
            consumed.get("protein_g")
            if consumed.get("protein_g") is not None
            else (consumed.get("protein") if consumed.get("protein") is not None else consumed.get("203"))
        )
        carbs_g = (
            consumed.get("carbs_g")
            if consumed.get("carbs_g") is not None
            else (
                consumed.get("carbs")
                if consumed.get("carbs") is not None
                else (consumed.get("carbohydrates") if consumed.get("carbohydrates") is not None else consumed.get("205"))
            )
        )
        fat_g = (
            consumed.get("fat_g")
            if consumed.get("fat_g") is not None
            else (consumed.get("fat") if consumed.get("fat") is not None else consumed.get("204"))
        )

        if consumed:
            totals["consumed"] = {
                "kcal": kcal,
                "protein_g": protein_g,
                "carbs_g": carbs_g,
                "fat_g": fat_g,
                **consumed,
            }
        elif "consumed" in raw_diary:
            totals["consumed"] = raw_diary["consumed"]

        for k in ("burned", "complete", "net", "remaining"):
            if k in summary:
                totals[k] = summary[k]
            elif k in raw_diary:
                totals[k] = raw_diary[k]

    return {
        "date": day,
        "summary": summary,
        "totals": totals,
        "entries": entries_list,
        "raw": raw_diary,
    }


def parse_food_search_response(query: str, raw: Any) -> Dict[str, Any]:
    raw_foods: List[Any] = []
    if isinstance(raw, list):
        raw_foods = raw
    elif isinstance(raw, dict):
        for key in ("foods", "results", "items", "hits"):
            if isinstance(raw.get(key), list):
                raw_foods = raw[key]
                break

    foods: List[Dict[str, Any]] = []
    for item in raw_foods:
        if not isinstance(item, dict):
            continue
        fid = item.get("id") if item.get("id") is not None else item.get("foodId")
        measure_id = item.get("measureId")
        measure_display = item.get("measureDisplayName") or ""
        source = item.get("source") or item.get("brand") or item.get("brandName") or ""

        raw_servings = item.get("servings") or item.get("measures") or []
        servings: List[Dict[str, Any]] = []
        if isinstance(raw_servings, list) and raw_servings:
            for srv in raw_servings:
                if isinstance(srv, dict):
                    servings.append(
                        {
                            "measureId": srv.get("measureId") or srv.get("servingId") or srv.get("id"),
                            "name": srv.get("name") or srv.get("measureName") or srv.get("unit") or "",
                            "grams": srv.get("grams") or srv.get("weight"),
                            "amount": srv.get("amount", 1.0),
                            "isDefault": bool(srv.get("isDefault", False)),
                        }
                    )
        elif measure_id is not None:
            servings.append(
                {
                    "measureId": measure_id,
                    "name": measure_display,
                    "grams": None,
                    "amount": 1.0,
                    "isDefault": True,
                }
            )

        foods.append(
            {
                "id": fid,
                "foodId": fid,
                "name": item.get("name") or item.get("description") or "",
                "source": source,
                "brand": source,
                "type": item.get("type") or "FOOD",
                "category": item.get("category"),
                "score": item.get("score"),
                "measureId": measure_id,
                "measureDisplayName": measure_display,
                "servings": servings,
            }
        )

    return {
        "query": query,
        "foods": foods,
        "raw": raw,
    }


def parse_custom_foods_response(raw: Any) -> Dict[str, Any]:
    items_raw: List[Any] = []
    if isinstance(raw, list):
        items_raw = raw
    elif isinstance(raw, dict):
        for key in ("foods", "recipes", "meals", "myFoods", "customFoods", "items"):
            if isinstance(raw.get(key), list):
                items_raw.extend(raw[key])

    foods: List[Dict[str, Any]] = []
    for item in items_raw:
        if not isinstance(item, dict):
            continue
        fid = item.get("foodId") if item.get("foodId") is not None else item.get("id")
        foods.append(
            {
                "id": fid,
                "foodId": fid,
                "name": item.get("name") or item.get("description") or "",
                "type": item.get("type") or item.get("category") or "custom",
                "servings": item.get("servings") or item.get("measures") or [],
            }
        )
    return {
        "foods": foods,
        "raw": raw,
    }


def parse_targets_response(raw: Any) -> Dict[str, Any]:
    targets_list: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                targets_list.append(
                    {
                        "id": item.get("id"),
                        "name": item.get("name") or item.get("nutrientName"),
                        "min": item.get("min") if item.get("min") is not None else item.get("target"),
                        "max": item.get("max"),
                        "unit": item.get("unit") or "",
                        "vis": item.get("vis"),
                        "custom": item.get("custom"),
                    }
                )
    elif isinstance(raw, dict):
        raw_t = raw.get("targets") or raw.get("nutrientTargets") or raw.get("compositeList")
        if isinstance(raw_t, list):
            for item in raw_t:
                if isinstance(item, dict):
                    targets_list.append(
                        {
                            "id": item.get("id"),
                            "name": item.get("name") or item.get("nutrientName"),
                            "min": item.get("min") if item.get("min") is not None else item.get("target"),
                            "max": item.get("max"),
                            "unit": item.get("unit") or "",
                            "vis": item.get("vis"),
                            "custom": item.get("custom"),
                        }
                    )

    return {
        "targets": targets_list,
        "count": len(targets_list),
        "raw": raw,
    }


def parse_biometric_search_response(query: Optional[str], raw: Any) -> Dict[str, Any]:
    raw_list: List[Any] = []
    categories_map: Dict[str, str] = {}

    if isinstance(raw, list):
        raw_list = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("categoriesMap"), dict):
            for cid, cobj in raw["categoriesMap"].items():
                if isinstance(cobj, dict) and cobj.get("name"):
                    categories_map[str(cid)] = str(cobj["name"])

        for key in ("metrics", "biometrics", "results", "items"):
            if isinstance(raw.get(key), list):
                raw_list = raw[key]
                break

        if not raw_list and isinstance(raw.get("categoryMetricsMap"), dict):
            for cid, mlist in raw["categoryMetricsMap"].items():
                if isinstance(mlist, list):
                    for m in mlist:
                        if isinstance(m, dict):
                            m_copy = dict(m)
                            m_copy.setdefault("category", categories_map.get(str(cid), str(cid)))
                            raw_list.append(m_copy)

    # Map metricId -> category name if categoryMetricsMap is present alongside metrics list
    metric_cat_lookup: Dict[int, str] = {}
    if isinstance(raw, dict) and isinstance(raw.get("categoryMetricsMap"), dict):
        for cid, mlist in raw["categoryMetricsMap"].items():
            cat_label = categories_map.get(str(cid), str(cid))
            if isinstance(mlist, list):
                for m in mlist:
                    if isinstance(m, dict) and m.get("id") is not None:
                        metric_cat_lookup[int(m["id"])] = cat_label
                    elif isinstance(m, int):
                        metric_cat_lookup[m] = cat_label

    metrics: List[Dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        mid = item.get("metricId") if item.get("metricId") is not None else item.get("id")
        name = item.get("name") or item.get("metricName") or item.get("description") or ""
        if query and query.lower() not in str(name).lower():
            continue

        unit = item.get("unit") or item.get("defaultUnit") or ""
        units_list = item.get("units")
        if not unit and isinstance(units_list, list) and units_list:
            first_u = units_list[0]
            if isinstance(first_u, dict):
                unit = first_u.get("name") or first_u.get("unit") or ""

        cat = item.get("category") or (metric_cat_lookup.get(int(mid), "") if mid is not None else "")
        metrics.append(
            {
                "id": mid,
                "metricId": mid,
                "name": name,
                "unit": unit,
                "units": units_list if isinstance(units_list, list) else [],
                "category": cat,
            }
        )
    return {
        "query": query,
        "metrics": metrics,
        "raw": raw,
    }


def print_output(data: Any, as_json: bool, human_formatter: Optional[Any] = None) -> None:
    if as_json:
        print(json.dumps(data, indent=2, sort_keys=True))
    elif human_formatter is not None:
        human_formatter(data)
    else:
        print(json.dumps(data, indent=2))


def format_auth_login(data: Dict[str, Any]) -> None:
    status = "SUCCESS" if data.get("success") else "FAILED"
    print(f"Login {status}")
    if data.get("userId") is not None:
        print(f"User ID: {data.get('userId')}")
    if data.get("serviceLevel"):
        print(f"Service level: {data.get('serviceLevel')}")
    print(f"Session file: {data.get('sessionFile')}")
    if "cookieNames" in data:
        cookies = ", ".join(data.get("cookieNames") or []) or "(none)"
        print(f"Stored cookies: {cookies}")


def format_auth_status(data: Dict[str, Any]) -> None:
    logged = "true" if data.get("loggedIn") else "false"
    print(f"logged_in: {logged}")
    print(f"user_id: {data.get('userId')}")
    if data.get("serviceLevel"):
        print(f"service_level: {data.get('serviceLevel')}")
    print(f"session_file: {data.get('sessionFile')}")
    if data.get("error"):
        print(f"error: {data.get('error')}")


def format_diary_show(data: Dict[str, Any]) -> None:
    print(f"Diary for {data.get('date')}:")
    totals = data.get("totals") or {}
    consumed = totals.get("consumed")
    if isinstance(consumed, dict):
        print(
            f"Summary Consumed: kcal={consumed.get('kcal')} | "
            f"protein_g={consumed.get('protein_g')} | "
            f"carbs_g={consumed.get('carbs_g')} | "
            f"fat_g={consumed.get('fat_g')}"
        )
    elif consumed is not None:
        print(f"Summary Consumed: {consumed}")

    entries = data.get("entries") or []
    if not entries:
        print("Entries: (none)")
        return
    print("Entries:")
    for entry in entries:
        tm = entry.get("time") or "--:--:--"
        etype = entry.get("type") or "Serving"
        fid = entry.get("foodId")
        name_part = f" ({entry['name']})" if entry.get("name") else ""
        amt = entry.get("grams") if entry.get("grams") is not None else entry.get("amount")
        sid = entry.get("servingId") or entry.get("id")
        id_prefix = f"[{sid}] " if sid is not None else ""
        print(f"  {id_prefix}{tm} | type={etype} | foodId={fid}{name_part} | grams={amt}")


def format_write_result(data: Dict[str, Any]) -> None:
    if data.get("dryRun"):
        print("DRY RUN — request not sent:")
        print(json.dumps(data.get("payload", data), indent=2, sort_keys=True))
        return
    if data.get("servingId") is not None:
        print(f"Serving ID: {data['servingId']}")
    elif data.get("entryId") is not None:
        print(f"Entry ID: {data['entryId']}")
    if "deleted" in data:
        print(f"Deleted: {data['deleted']} (diary entries: {data.get('beforeCount')} -> {data.get('afterCount')})")
        return
    print("Server response:")
    print(json.dumps(data.get("response"), indent=2, sort_keys=True))


def format_food_search(data: Dict[str, Any]) -> None:
    foods = data.get("foods") or []
    if not foods:
        print(f"No foods found for query '{data.get('query')}'.")
        return
    for f in foods:
        fid = f.get("id") if f.get("id") is not None else f.get("foodId")
        src = f.get("source") or f.get("brand") or ""
        mid = f.get("measureId")
        mdisp = f.get("measureDisplayName") or ""
        print(f"- id={fid} | name={f.get('name')} | source={src} | measureId={mid} | measureDisplayName={mdisp}")


def format_custom_foods(data: Dict[str, Any]) -> None:
    foods = data.get("foods") or []
    if not foods:
        print("No custom foods found.")
        return
    for f in foods:
        print(f"- foodId={f.get('foodId')}: {f.get('name')} ({f.get('type')})")


def format_targets(data: Dict[str, Any]) -> None:
    targets = data.get("targets") or []
    print(f"Nutrient Targets ({len(targets)}):")
    for t in targets:
        tid = t.get("id")
        name = f" ({t['name']})" if t.get("name") else ""
        unit = f" {t['unit']}" if t.get("unit") else ""
        vis = t.get("vis")
        custom = t.get("custom")
        print(f"  nutrientId={tid}{name}: min={t.get('min')}{unit} | max={t.get('max')}{unit} | vis={vis} | custom={custom}")


def format_biometric_search(data: Dict[str, Any]) -> None:
    metrics = data.get("metrics") or []
    if not metrics:
        print("No biometrics found.")
        return
    for m in metrics:
        unit = f" ({m['unit']})" if m.get("unit") else ""
        cat = f" [{m['category']}]" if m.get("category") else ""
        print(f"- metricId={m.get('metricId')}: {m.get('name')}{unit}{cat}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crono",
        description="Cronometer CLI (read + write) via verified mobile V2/V3 APIs",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CRONOMETER_BASE_URL", DEFAULT_BASE_URL),
        help=f"Override mobile API V2 base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--session-file",
        default=os.environ.get("CRONOMETER_SESSION_FILE", DEFAULT_SESSION_FILE),
        help=f"Override session file path (default: {DEFAULT_SESSION_FILE})",
    )

    subparsers = parser.add_subparsers(dest="group", required=True)

    # 1 & 2: auth
    auth_parser = subparsers.add_parser("auth", help="Authentication & session commands")
    auth_sub = auth_parser.add_subparsers(dest="command", required=True)

    auth_login = auth_sub.add_parser("login", help="Log in via mobile API V2 and store userId + sessionKey")
    auth_login.add_argument("--totp", default=None, help="Optional 6-digit 2FA TOTP code (or CRONOMETER_TOTP)")

    auth_web_login = auth_sub.add_parser("web-login", help="Log in via legacy web form and store session cookies")
    auth_web_login.add_argument("--totp", default=None, help="Optional 6-digit 2FA TOTP code (or CRONOMETER_TOTP)")

    auth_sub.add_parser("status", help="Check whether the saved mobile session is valid via V2 session-resume")

    # 3, 4, 5: diary
    diary_parser = subparsers.add_parser("diary", help="Daily diary inspection and modification")
    diary_sub = diary_parser.add_subparsers(dest="command", required=True)

    diary_show = diary_sub.add_parser("show", help="Show diary entries and summary totals for a date")
    diary_show.add_argument(
        "--date",
        default=None,
        help="Target date in YYYY-MM-DD (defaults to today for 'diary show' only)",
    )

    diary_add = diary_sub.add_parser("add", help="Add a food serving entry to the diary via V2 add_serving")
    diary_add.add_argument("--food-id", type=int, required=True, help="Food ID")
    diary_add.add_argument("--measure-id", type=int, required=True, help="Measure ID")
    diary_add.add_argument("--amount", type=float, required=True, help="Amount in grams")
    diary_add.add_argument("--date", required=True, help="Required explicit entry date YYYY-MM-DD")
    diary_add.add_argument("--time", required=True, help="Required explicit entry time HH:MM or HH:MM:SS")
    diary_add.add_argument("--group-id", "--group", dest="group_id", type=int, default=None, help="Optional diary meal group / order")
    diary_add.add_argument("--order", type=int, default=None, help="Optional diary display order")
    diary_add.add_argument("--dry-run", action="store_true", help="Print exact V2 request body without sending")

    diary_del = diary_sub.add_parser("delete", help="Delete a diary serving entry via V3 diary-entries")
    diary_del.add_argument("--serving-id", "--entry-id", dest="serving_id", type=int, required=True, help="Serving ID to delete")
    diary_del.add_argument("--date", required=True, help="Required date YYYY-MM-DD of the diary entry")
    diary_del.add_argument("--dry-run", action="store_true", help="Print delete payload without sending request")

    # 6 & 7: food
    food_parser = subparsers.add_parser("food", help="Search foods and list custom foods")
    food_sub = food_parser.add_subparsers(dest="command", required=True)

    food_search = food_sub.add_parser("search", help="Search food database via V3 food-search/string")
    food_search.add_argument("query", help="Search query string")

    food_sub.add_parser("custom", help="List custom foods and recipes via V2 get_myfoods")

    # 8: targets
    targets_parser = subparsers.add_parser("targets", help="Inspect nutrient targets via V3 targets")
    targets_sub = targets_parser.add_subparsers(dest="command", required=True)
    targets_sub.add_parser("show", help="Show user nutrient targets")

    # 9 & 10: biometric
    bio_parser = subparsers.add_parser("biometric", help="Log or search biometric entries via V2")
    bio_sub = bio_parser.add_subparsers(dest="command", required=True)

    bio_log = bio_sub.add_parser("log", help="Log a biometric measurement via V2 add_biometric")
    bio_log.add_argument("--metric-id", type=int, required=True, help="Biometric metric ID")
    bio_log.add_argument("--value", type=float, required=True, help="Numeric measurement value")
    bio_log.add_argument("--unit-id", type=int, default=None, help="Optional biometric unit ID")
    bio_log.add_argument("--date", required=True, help="Required explicit entry date YYYY-MM-DD")
    bio_log.add_argument("--time", required=True, help="Required explicit entry time HH:MM or HH:MM:SS")
    bio_log.add_argument("--dry-run", action="store_true", help="Print payload without sending request")

    bio_search = bio_sub.add_parser("search", help="Browse or search biometric definitions via V2 get_metrics")
    bio_search.add_argument("query", nargs="?", default=None, help="Optional biometric search query")

    return parser


def _prompt_credentials(args_totp: Optional[str]) -> tuple[str, str, Optional[str]]:
    user = os.environ.get("CRONOMETER_USER")
    if not user:
        user = input("Cronometer email: ").strip()
    password = os.environ.get("CRONOMETER_PASS")
    if not password:
        password = getpass.getpass("Cronometer password: ")
    if not user or not password:
        raise CronoError("Email and password are required (via CRONOMETER_USER/CRONOMETER_PASS or prompt)")
    totp = args_totp if args_totp is not None else os.environ.get("CRONOMETER_TOTP")
    return user, password, totp


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    client = CronoClient(
        base_url=args.base_url,
        session_file=args.session_file,
    )

    try:
        if args.group == "auth" and args.command == "login":
            user, password, totp = _prompt_credentials(args.totp)
            res = client.login(email=user, password=password, totp=totp)
            print_output(res, args.json, format_auth_login)

        elif args.group == "auth" and args.command == "web-login":
            user, password, totp = _prompt_credentials(args.totp)
            res = client.web_login(username=user, password=password, totp=totp)
            print_output(res, args.json, format_auth_login)

        elif args.group == "auth" and args.command == "status":
            res = client.check_status()
            print_output(res, args.json, format_auth_status)

        elif args.group == "diary" and args.command == "show":
            date_str = args.date or datetime.date.today().isoformat()
            res = client.get_diary(date_str)
            print_output(res, args.json, format_diary_show)

        elif args.group == "diary" and args.command == "add":
            res = client.add_diary_entry(
                food_id=args.food_id,
                measure_id=args.measure_id,
                amount=args.amount,
                date_str=args.date,
                time_str=args.time,
                group_id=args.group_id,
                order=args.order,
                dry_run=args.dry_run,
            )
            print_output(res, args.json, format_write_result)

        elif args.group == "diary" and args.command == "delete":
            res = client.delete_diary_entry(
                serving_id=args.serving_id,
                date_str=args.date,
                dry_run=args.dry_run,
            )
            print_output(res, args.json, format_write_result)

        elif args.group == "food" and args.command == "search":
            res = client.search_food(args.query)
            print_output(res, args.json, format_food_search)

        elif args.group == "food" and args.command == "custom":
            res = client.get_custom_foods()
            print_output(res, args.json, format_custom_foods)

        elif args.group == "targets" and args.command == "show":
            res = client.get_targets()
            print_output(res, args.json, format_targets)

        elif args.group == "biometric" and args.command == "log":
            res = client.log_biometric(
                metric_id=args.metric_id,
                value=args.value,
                date_str=args.date,
                time_str=args.time,
                unit_id=args.unit_id,
                dry_run=args.dry_run,
            )
            print_output(res, args.json, format_write_result)

        elif args.group == "biometric" and args.command == "search":
            res = client.search_biometrics(args.query)
            print_output(res, args.json, format_biometric_search)

        else:
            parser.print_help()
            return 2

    except CronoError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
