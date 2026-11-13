# Pilot Audit Report — `cronometer-cli`

## 1. What the CLI Does

`crono` (`crono.py`) is a Python 3 command-line client for Cronometer that communicates with Cronometer's reverse-engineered mobile V2 RPC API (`https://mobile.cronometer.com/api/v2/`) and V3 REST API (`https://mobile.cronometer.com/api/v3/`), alongside a legacy web-form login path (`https://cronometer.com/login`).

Supported command groups:
- **`auth`**: `login` (mobile V2 email/password + optional `--totp`), `web-login` (legacy web form + CSRF cookie capture), and `status` (V2 session-resume check with rotation persistence and in-place self-healing).
- **`diary`**: `show` (fetch daily entries, resolve food names via `get_foods`, display macro totals), `add` (log a food serving via `add_serving`, requiring explicit `--date` and `--time`, with `--dry-run`), `delete` (locate entry via `get_diary`, delete via V3 `DELETE /user/{userId}/diary-entries`, and verify removal), and `export` (iterate `get_diary` over a date range up to 366 days in JSON or CSV).
- **`food`**: `search` (V3 `food-search/string`) and `custom` (V2 `get_myfoods`).
- **`targets`**: `show` (V3 `user/{userId}/targets`).
- **`biometric`**: `log` (V2 `add_biometric` with required `--date`/`--time` and `--dry-run`) and `search` (V2 `get_metrics`).

Cross-cutting features include `--json` machine-readable output, secret redaction in errors and dry-run output, and automatic session durability (rotation persistence, auto-relogin + single retry on auth failures, and `keepalive.py` for proactive token re-minting).

## 2. Main Modules

- **`crono.py`** (1,663 lines): Single-file CLI implementing constants, `CronoError`, session/credential file I/O (`0600`-enforced), date/time validation, V2 payload builders, `CronoClient` HTTP wrapper (`requests.Session` with 20s timeout), response normalizers/parsers, human-readable formatters, `argparse` CLI definition (`build_parser`), and `main`/`_main_once` with auto-relogin retry.
- **`keepalive.py`** (179 lines): Daily cron driver that checks session health via `CronoClient.check_status()`, proactively re-mints tokens older than `--max-age-days` (default `5.0`), heals dead sessions from stored credentials, and records status/timestamps in `~/.config/cronometer-cli/keepalive-state.json` (`0600`), exiting `0` (healthy), `2` (dead after retry), or `3` (dead, no credentials).
- **`tests/test_crono.py` & `tests/test_keepalive.py`**: Existing `unittest` suites (23 tests) covering mocked HTTP interactions, error envelopes, secret redaction, and session durability/keepalive flows.
- **Supporting docs/config**: `README.md`, `DURABILITY.md`, `credentials.example`, and `requirements.txt` (`requests` only).

## 3. Obvious Gaps, Missing Tests, and Rough Edges

### Missing Tests (addressed in part by the new pilot test suite)
- **Argument parsing**: Existing tests only checked missing `--date`/`--time` on `diary add` and `biometric log`. There were no tests for `build_parser()` across `auth`, `diary show`/`delete`/`export`, `food`, `targets`, `biometric search`, global flags (`--json`, `--base-url`, `--session-file`), or flag aliases (`--group-id`/`--group`, `--serving-id`/`--entry-id`).
- **Formatting & non-network helpers**: No direct unit tests for `format_diary_show`, `format_food_search`, `format_custom_foods`, `format_write_result`, `print_output`, `validate_date`/`validate_time` edge cases, `_normalize_entry` measure resolution, or `build_serving_object` optional `group_id`/`order` behavior.
- **`diary export`**: `export_diary` (date-range validation, 366-day cap, `--to < --from`) and CSV formatting had no automated tests.

### Rough Edges & Functional Gaps
- **No `TODO`/`FIXME` markers**, but several endpoints remain **AOT-reconstructed without live verification** (`food custom`, `biometric log`, `biometric search`), and exercise/note logging (`add_exercise`, `add_note`) is unimplemented due to uncaptured payload schemas.
- **Global flag placement**: `--json`, `--base-url`, and `--session-file` are registered only on the top-level parser, so passing them after a subcommand (e.g., `crono.py diary show --json`) fails `argparse`.
- **Hardcoded client metadata**: `APP_TIMEZONE` (`"America/New_York"`), `APP_VERSION`, `APP_BUILD_NUMBER`, and `APP_DEVICE_STRING` are fixed constants in `crono.py`.
- **Monolithic structure & no packaging**: All logic resides in a single 1,663-line `crono.py` with no `pyproject.toml`/editable install entry point.
- **Inline CSV formatting**: `diary export --format csv` formats CSV inline inside `_main_once` rather than using a dedicated formatter function like the other commands.
- **Unattended 2FA limitation**: Auto-relogin and `keepalive.py` assume 2FA is disabled (no TOTP generation/hook for unattended re-minting).
- **Dangling doc references**: `README.md` and `DURABILITY.md` reference `AUTH-FINDINGS.md` and `AUTH-FINDINGS-2.md`, which are not present in the repository.
