# `crono` — Cronometer CLI (Read + Write via Verified Mobile V2/V3 APIs)

Single-file Python 3 CLI talking to Cronometer's verified mobile V2 RPC API (`https://mobile.cronometer.com/api/v2/`) and V3 REST API (`https://mobile.cronometer.com/api/v3/`), reconstructed via Dart AOT disassembly (`AUTH-FINDINGS-2.md`) and live-verified end-to-end on 2026-10-18.

---

## Installation & Setup

### Requirements
- Python 3.9+
- `requests` (`pip install -r requirements.txt`)

```bash
cd ~/workspace/cronometer-cli
pip install -r requirements.txt
python3 crono.py --help
```

---

## Global Flags

All global flags are passed before the command group:

- `--json` — Emit machine-readable JSON output to stdout (and JSON error objects to stderr).
- `--base-url URL` — Override the mobile API V2 base URL (default: `https://mobile.cronometer.com/api/v2/`, or `CRONOMETER_BASE_URL` env var; `/api/v2/` is automatically mapped to `/api/v3/` for V3 calls).
- `--session-file PATH` — Override the session file location (default: `~/.config/cronometer-cli/session.json`, or `CRONOMETER_SESSION_FILE` env var).

---

## Authentication Flow & Session Format

### Primary Mobile Login (`auth login`)
1. Reads credentials from `CRONOMETER_USER` and `CRONOMETER_PASS` environment variables, or prompts interactively (`getpass` for password). Credentials and tokens are **never** hardcoded or printed to stdout/stderr.
2. Reads optional 6-digit 2FA TOTP code from `--totp` or `CRONOMETER_TOTP` (sent in `"userCode"`). If 2FA is required and not provided, the server returns `{"error": "TOTP_CODE_REQUIRED"}`.
3. Sends `POST https://mobile.cronometer.com/api/v2/login` with the full Android v4.58.3 (build 3541) JSON body:
   - `"email"`, `"password"`, `"timezone"`, `"userCode"`, `"build": "4.58.3 b3541-a"`, `"device"`, `"firebaseToken": null`
   - `"auth": {"userId": null, "token": null, "api": 3, "os": "Android", "build": "3541", "flavour": null}`
   - `"lastSeen": 0`, `"config": {"call_version": 4}`
4. Extracts `id` (integer `userId`) and `sessionKey` (32-character token) from the HTTP 200 JSON response and saves them to `~/.config/cronometer-cli/session.json` with `0600` permissions:
   ```json
   {
     "sessionKey": "<32_CHAR_TOKEN>",
     "updatedAt": "2026-10-18T20:25:00+00:00",
     "userId": 1234567
   }
   ```

### Session Validation (`auth status`)
- Sends a session-resume `POST https://mobile.cronometer.com/api/v2/login` (`API::loginWithSessionKey` shape: no `email`/`password`, `auth` populated with saved `userId` and `token: sessionKey`) and reports whether the session is active.

### Legacy Web Login (`auth web-login`)
- Preserved separately as `python3 crono.py auth web-login` (`GET /login/` for `anticsrf` + `POST https://cronometer.com/login`), storing web cookies in the session file.

---

## Mobile API Endpoint Map: V2 RPC vs V3 REST

| Command | API Layer | Method & Path | Auth Mechanism | Verification Status |
|---|---|---|---|---|
| `auth login` | **V2 RPC** | `POST /api/v2/login` (with `email` + `password`) | `body["auth"]` (`null` userId/token) | **Live-verified** |
| `auth status` | **V2 RPC** | `POST /api/v2/login` (no `email`/`password`) | `body["auth"]` (`{userId, token}`) | **Live-verified** |
| `diary show` | **V2 RPC** | `POST /api/v2/get_diary` + `POST /api/v2/get_foods` | `body["auth"]` + `lastSeen: 0` | **Live-verified** |
| `diary add` | **V2 RPC** | `POST /api/v2/add_serving` (`config: {call_version: 2}`) | `body["auth"]` + `lastSeen: 0` | **Live-verified** |
| `diary delete` | **V2 + V3** | `POST /api/v2/get_diary` → `DELETE /api/v3/user/{userId}/diary-entries` → `POST /api/v2/get_diary` | V2 `body["auth"]` + V3 `X-CRONO-SESSION` | **Live-verified (204)** |
| `food search` | **V3 REST** | `GET /api/v3/user/{userId}/food-search/string?query=<q>&selectedTab=ALL` | Header `X-CRONO-SESSION` + `X-CRONO-APP-*` | **Live-verified** |
| `food custom` | **V2 RPC** | `POST /api/v2/get_myfoods` (`config: {call_version: 2}`) | `body["auth"]` + `lastSeen: 0` | AOT-reconstructed (response shape unverified live) |
| `targets show` | **V3 REST** | `GET /api/v3/user/{userId}/targets` | Header `X-CRONO-SESSION` + `X-CRONO-APP-*` | **Live-verified (57-item list)** |
| `biometric log` | **V2 RPC** | `POST /api/v2/add_biometric` | `body["auth"]` + `lastSeen: 0` | AOT-reconstructed |
| `biometric search` | **V2 RPC** | `POST /api/v2/get_metrics` (`{"includeForest": true}`) | `body["auth"]` + `lastSeen: 0` | AOT-reconstructed |

---

## Commands & Examples

### 1. `auth login` (and `auth web-login`)
```bash
export CRONOMETER_USER="user@example.com"
export CRONOMETER_PASS="..."
python3 crono.py auth login

# With 2FA TOTP:
python3 crono.py auth login --totp 123456

# Legacy web-form login (kept separate):
python3 crono.py auth web-login
```

### 2. `auth status`
Validate cached `userId` + `sessionKey` via V2 session-resume:
```bash
python3 crono.py auth status
python3 crono.py --json auth status
```

### 3. `diary show [--date YYYY-MM-DD]`
Fetch diary entries (`POST /api/v2/get_diary`), resolve food names via `POST /api/v2/get_foods` when needed, and render entries + `summary.consumed` totals (`kcal`, `protein_g`, `carbs_g`, `fat_g`):
```bash
python3 crono.py diary show
python3 crono.py diary show --date 2026-10-17
python3 crono.py --json diary show --date 2026-10-17
```

### 4. `diary add`
Add a food serving entry (`POST /api/v2/add_serving`).
Note: `--date` and `--time` are **required explicit flags** and never default to today/now. `--amount` is in grams.
```bash
# Dry-run (prints exact V2 request body without network call):
python3 crono.py diary add \
  --food-id 464785 \
  --measure-id 1072753 \
  --amount 210 \
  --date 2026-10-18 \
  --time 08:30 \
  --dry-run

# Live write:
python3 crono.py diary add \
  --food-id 464785 \
  --measure-id 1072753 \
  --amount 210 \
  --date 2026-10-18 \
  --time 08:30
```

### 5. `diary delete`
Delete a diary entry by `--serving-id` and `--date` (`DELETE /api/v3/user/{userId}/diary-entries`). Fetches that day's diary first to locate the exact entry object, sends the V3 `DELETE` (204 No Content), and re-fetches the diary to verify removal:
```bash
python3 crono.py diary delete --serving-id 5312075123 --date 2026-10-18 --dry-run
python3 crono.py diary delete --serving-id 5312075123 --date 2026-10-18
```

### 6. `food search QUERY`
Search foods (`GET /api/v3/user/{userId}/food-search/string?query=QUERY&selectedTab=ALL`) and render `id`, `name`, `source`, `measureId`, and `measureDisplayName`:
```bash
python3 crono.py food search "banana"
python3 crono.py --json food search "greek yogurt"
```

### 7. `food custom`
List user-created custom foods, recipes, and meals via `POST /api/v2/get_myfoods` (note: endpoint name and parser `MyFood::buildMyFoods` are AOT-verified, though live response item fields are unverified on an account with custom foods):
```bash
python3 crono.py food custom
```

### 8. `targets show`
Show user nutrient targets (`GET /api/v3/user/{userId}/targets`, returning a list of 57 target objects):
```bash
python3 crono.py targets show
python3 crono.py --json targets show
```

### 9. `biometric log`
Log a biometric measurement (`POST /api/v2/add_biometric`). `--date` and `--time` are required explicit flags:
```bash
python3 crono.py biometric log \
  --metric-id 1 \
  --value 75.4 \
  --date 2026-10-18 \
  --time 07:00 \
  --dry-run
```

### 10. `biometric search [QUERY]`
List biometric metric definitions (`POST /api/v2/get_metrics` with `{"includeForest": true}`) and filter by `QUERY`:
```bash
python3 crono.py biometric search
python3 crono.py biometric search "Weight"
```

---

## Error Handling & Security
- **Top-level `"error"` in HTTP 200 JSON**: Raised as `CronoError` (`API error: <message>`), matching `_MixinApplication5&Object&APIService::handleHttpPostResponse`.
- **Legacy `{"result": "FAIL", "error": "..."}`**: Also raised as `CronoError`.
- **HTTP 401 / 403**: Reports `session missing or expired, run auth login`.
- **Secret Protection**: `sessionKey` and `password` values are never printed in CLI output, error messages, or `--dry-run` payloads.
- **Timeouts**: All HTTP calls use a strict 20-second timeout.

---

## Running Offline Unit Tests
```bash
python3 -m unittest discover -s tests -v
```
