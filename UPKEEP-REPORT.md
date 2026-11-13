# Weekly Upkeep Audit Report — `cronometer-cli`

**Date:** 2026-11-13  
**Branch:** `remote-agent/upkeep-20261113`  
**Base commit:** `b858339` (`main`)

## Executive Summary

No mechanical fixes were required. All 23 unit tests pass, `README.md` and `DURABILITY.md` match the CLI's `--help` surface, there are no TODO/FIXME comments or dead markdown links, and `requirements.txt` contains no outdated version pins.

---

## Findings by Severity

### Critical / High / Medium
- **None.**

### Low / Informational
1. **Uncommitted external references in documentation (`README.md`, `DURABILITY.md`):**
   - `README.md` (line 3) and `DURABILITY.md` (lines 9, 17) reference `AUTH-FINDINGS-2.md`, and `DURABILITY.md` (line 10) references `AUTH-FINDINGS.md`. Neither file is present in the repository.
   - `README.md` (line 14) and `DURABILITY.md` (lines 11, 13) also mention local workspace paths (`~/workspace/cronometer-cli`, `~/workspace/nutrition/cronometer-notes.md`).
   - These are backticked historical/contextual notes rather than broken Markdown links, so they were left intact.
2. **Unpinned dependency (`requirements.txt`):**
   - `requirements.txt` specifies a single unpinned package (`requests`, resolved to `2.34.2` in the test environment). There are no outdated version pins.
3. **Optional flags omitted from `README.md` examples:**
   - `diary add --help` includes optional `--group-id` (`--group`) and `--order`, and `biometric log --help` includes optional `--unit-id`. These are not shown in the `README.md` usage examples, though `README.md` does not claim to exhaustively enumerate optional flags.
4. **Repository age (< 30 days):**
   - Initial commit `fd0bda7` is dated `2026-10-19` (25 days prior to this audit). No `TODO`, `FIXME`, `XXX`, or `HACK` comments exist anywhere in the codebase.

---

## Audit Details

### 1. Documentation vs. CLI `--help` Surface
Verified `python3 crono.py --help`, all 5 command groups (`auth`, `diary`, `food`, `targets`, `biometric`), all 12 leaf subcommands (`auth login`, `auth web-login`, `auth status`, `diary show`, `diary add`, `diary delete`, `diary export`, `food search`, `food custom`, `targets show`, `biometric log`, `biometric search`), and `python3 keepalive.py --help` against `README.md` and `DURABILITY.md`:
- Global flags (`--json`, `--base-url`, `--session-file`) and environment variables (`CRONOMETER_BASE_URL`, `CRONOMETER_SESSION_FILE`, `CRONOMETER_USER`, `CRONOMETER_PASS`, `CRONOMETER_TOTP`, `CRONO_NO_AUTOLOGIN`) match.
- All 12 `crono.py` subcommands, required flags, and `keepalive.py` behavior (5-day proactive re-mint threshold, state file, exit codes `0`/`2`/`3`) match.

### 2. Test Suite
- Ran `python -m unittest discover -s tests -v`: **23/23 passed** (`0.082s`).
- Ran `python -m pytest -v`: **23/23 passed** (`0.19s`).
- No failing tests or CLI bugs observed. (Note: running bare `pytest` without `python -m pytest` or `PYTHONPATH=.` fails test collection because the repo root is not on `sys.path`; `README.md` documents `python3 -m unittest discover -s tests -v`, which works out of the box.)

### 3. Stale Version Strings, Dead Links, and TODO/FIXME Comments
- **TODO/FIXME/XXX/HACK:** 0 matches across all tracked files.
- **Version strings:** `APP_VERSION = "4.58.3"`, `APP_BUILD_NUMBER = "3541"`, `APP_BUILD_STRING = "4.58.3 b3541-a"` in `crono.py` match `README.md` (`v4.58.3 (build 3541)`).
- **Links:** No broken Markdown links. External URLs are Cronometer API endpoints (`https://mobile.cronometer.com/api/v2/`, `https://mobile.cronometer.com/api/v3/`, `https://cronometer.com/login`) and the repository URL (`https://github.com/jonathancruzate/cronometer-cli`).

### 4. Dependency Hygiene
- `requirements.txt` contains only `requests` (unpinned). No `pyproject.toml`, `setup.py`, or lockfiles exist.
- Installed in isolated test virtualenv (`/tmp/crono-venv`): `requests==2.34.2` (with `certifi==2026.10.23`, `charset-normalizer==3.5.2`, `idna==3.20`, `urllib3==2.8.0`).

---

## What Was Changed
- Added `UPKEEP-REPORT.md` at the repository root.

## What Was Deliberately Not Changed (and Why)
- **`crono.py`, `keepalive.py`, `tests/*`:** All 23 tests pass; no CLI behavior changes or test fixes were needed.
- **`README.md`, `DURABILITY.md`:** All documented commands, flags, defaults, and exit codes match `--help`. Historical references to `AUTH-FINDINGS.md` / `AUTH-FINDINGS-2.md` and local paths were preserved for context.
- **`requirements.txt`:** Kept `requests` unpinned to avoid introducing version constraints or upgrading dependencies that could alter CLI behavior.
