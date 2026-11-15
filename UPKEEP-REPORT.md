# Weekly Upkeep Audit Report — `cronometer-cli`

**Date:** 2026-11-15  
**Branch:** `remote-agent/upkeep-20261115`  
**Base commit:** `b858339` (`main`)

## Executive Summary

No mechanical code or documentation fixes were required. All 23 offline unit tests pass (`python3 -m unittest discover -s tests -v`), `README.md`, `DURABILITY.md`, and `credentials.example` accurately match the CLI `--help` surface of `crono.py` and `keepalive.py`, no `TODO`/`FIXME` comments or broken links exist, and `requirements.txt` has no outdated version pins.

---

## Findings by Severity

### Critical / High / Medium
- **None.**

### Low / Informational
1. **Unpinned dependency in `requirements.txt` & older host packages:**
   - `requirements.txt` lists `requests` without a version pin or lockfile, so there are no outdated pins in the repository itself.
   - In the audit host environment (`Python 3.11.2`), the installed `requests` package is `2.28.1` (with `urllib3==1.26.12`, `certifi==2022.9.24`, `charset-normalizer==3.0.1`, `idna==3.3`), which lags current upstream releases (`requests 2.32.x` / `urllib3 2.x`).
2. **External / local-workspace references in documentation (`README.md`, `DURABILITY.md`, `crono.py`):**
   - `README.md` (line 3), `DURABILITY.md` (lines 9, 10, 17), and `crono.py` comments (lines 102, 112, 620, 1635) reference `AUTH-FINDINGS.md` and `AUTH-FINDINGS-2.md`, which are external reverse-engineering notes not tracked in this repository.
   - `README.md` (line 14) and `DURABILITY.md` (lines 11, 13) reference local paths (`~/workspace/cronometer-cli`, `~/workspace/nutrition/cronometer-notes.md`, `~/.config/agent-google/password`).
   - These are inline code/path citations rather than hyperlinked Markdown URLs and serve as historical provenance.
3. **Optional flags and aliases omitted from `README.md` examples:**
   - `diary add --help` supports optional `--group-id` (`--group`) and `--order`.
   - `diary delete --help` supports `--entry-id` as an alias for `--serving-id`.
   - `biometric log --help` supports optional `--unit-id`.
   - `README.md` illustrates the required flags and `--dry-run` in its examples without claiming to list every optional flag; there is no contradiction with `--help`.
4. **Post-delete verification ID check in `crono.py` (`delete_diary_entry`):**
   - Prior to deletion (`crono.py:779`), entry lookup checks `servingId`, `biometricId`, `exerciseId`, `noteId`, and `id`. Post-deletion verification (`crono.py:804`) checks `servingId`, `biometricId`, and `id` (omitting `exerciseId` and `noteId`). Kept unchanged per the non-goal of no CLI behavior changes.

---

## Audit Details

### 1. README and Docs vs. CLI `--help` Surface
Verified `python3 crono.py --help`, all 5 command groups (`auth`, `diary`, `food`, `targets`, `biometric`), all 12 leaf subcommands, and `python3 keepalive.py --help` against `README.md`, `DURABILITY.md`, and `credentials.example`:
- **Global options:** `--json`, `--base-url` (default `https://mobile.cronometer.com/api/v2/`, env `CRONOMETER_BASE_URL`), `--session-file` (default `~/.config/cronometer-cli/session.json`, env `CRONOMETER_SESSION_FILE`).
- **Subcommands verified:**
  - `auth login [--totp TOTP]`
  - `auth web-login [--totp TOTP]`
  - `auth status`
  - `diary show [--date DATE]`
  - `diary add --food-id FOOD_ID --measure-id MEASURE_ID --amount AMOUNT --date DATE --time TIME [--group-id GROUP_ID] [--order ORDER] [--dry-run]`
  - `diary delete --serving-id SERVING_ID --date DATE [--dry-run]`
  - `diary export [--from FROM_DATE] [--to TO_DATE] [--format {json,csv}]`
  - `food search query`
  - `food custom`
  - `targets show`
  - `biometric log --metric-id METRIC_ID --value VALUE [--unit-id UNIT_ID] --date DATE --time TIME [--dry-run]`
  - `biometric search [query]`
- **Keep-alive CLI (`keepalive.py --help`):** `--session-file`, `--state-file` (default `~/.config/cronometer-cli/keepalive-state.json`, env `CRONO_KEEPALIVE_STATE`), `--max-age-days` (default `5`), and exit codes `0` / `2` / `3` match `README.md` and `DURABILITY.md`.

### 2. Test Suite
- Executed `python3 -m unittest discover -s tests -v`.
- **Result:** 23/23 tests passed (`19` in `tests/test_crono.py`, `4` in `tests/test_keepalive.py`).
- No failing tests or CLI bugs were triggered.

### 3. Stale Version Strings, Dead Links, and TODO/FIXME Comments
- **TODO / FIXME / XXX / HACK comments:** None found (`0` matches across all tracked files; initial commit `fd0bda7` was authored on `2026-10-19`, 27 days before this audit).
- **Version strings:** `APP_VERSION = "4.58.3"`, `APP_BUILD_NUMBER = "3541"`, and `APP_BUILD_STRING = "4.58.3 b3541-a"` in `crono.py` match `README.md` and `tests/test_crono.py`.
- **Links:** All URLs in `README.md` (`https://mobile.cronometer.com/api/v2/`, `https://mobile.cronometer.com/api/v3/`, `https://cronometer.com/login`, `https://github.com/jonathancruzate/cronometer-cli`) are valid and consistent with the codebase.

### 4. Dependency Hygiene
- `requirements.txt` contains a single unpinned requirement (`requests`).
- No pinned dependencies are outdated in `requirements.txt`. Host environment packages (`requests==2.28.1`, `urllib3==1.26.12`, `certifi==2022.9.24`, `charset-normalizer==3.0.1`, `idna==3.3`) are noted above for visibility.

---

## What Was Changed
- Committed `UPKEEP-REPORT.md` at the repository root on branch `remote-agent/upkeep-20261115`.

## What Was Deliberately Not Changed (and Why)
- **`crono.py`, `keepalive.py`, `tests/`:** All 23 unit tests pass and no CLI behavior changes are permitted.
- **`README.md`, `DURABILITY.md`, `credentials.example`:** All documented commands, flags, environment variables, and defaults match `--help` output; historical notes referencing `AUTH-FINDINGS*.md` and local paths were left intact as provenance context.
- **`requirements.txt`:** Left unpinned (`requests`) to avoid introducing dependency changes that could affect CLI behavior or downstream environments.
