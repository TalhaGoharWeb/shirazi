# SHIRAZI — Security Audit (Phase 9)

Date: 2026-09-23. Scope: working tree at Phase 9 completion (Linux CI; Windows
paths static-only). Method: static scans + targeted unit tests + direct
behaviour checks. No environment was dumped; no secret values were read.

## 1. Secret scan

- Scanned the tracked tree (excluding `.git`, `archive`, `__pycache__`) for
  common provider-key patterns (Gemini `AIza…`, OpenAI `sk-…`, GitHub
  `ghp_/github_pat_`, Slack `xox`, AWS `AKIA…`): **0 matches**.
- Filename scan for secret-like names: only `core/accounts/secrets.py`
  (implementation, no values).
- `dashboard/static/`: no API-key fields, no key values, no token literals.
- Frontend key boundary: the dashboard API returns only
  `key_configured: bool` per provider — **no key values are ever returned to
  the browser**. Verified by reading the provider-status endpoints.

## 2. Ignore verification (real defect found and fixed)

`.gitignore` had inline trailing comments on several patterns, which git does
**not** support — the patterns were silently ineffective. Fixed by moving all
inline comments onto standalone comment lines, then verified with
`git check-ignore`:

| Path | Ignored? |
|---|---|
| `config/api_keys.json` | ✅ |
| `config/certs/` | ✅ |
| `config/whatsapp_web/` | ✅ |
| `token*.json` (nested + root) | ✅ |
| `client_secret*.json` (nested + root) | ✅ |
| `config/.env`, `.env.*` | ✅ |
| `config/shirazi_memory.db`, `config/*.db` | ✅ |
| `config/dashboard.json` | ✅ |

## 3. Device auth / session revocation (real bugs found and fixed)

Phase 9 tests found two bugs; both are fixed and covered by
`tests/test_phase9_hardening.py::TestAuthHardening` (7 tests):

1. **Empty channel key on new devices.** `POST /api/v1/devices` created devices
   with an empty `session_key`, so `/api/device-login` could not exchange
   their tokens after a restart. Fix: `core/accounts/devices.py` now mints a
   random channel key when the caller supplies none.
2. **Revocation did not fully invalidate bearers.** In-memory `_tokens`
   survived revocation; adopted session records lacked `device_id`; and
   `revoke_all_devices()` did not cascade to sessions. Fixes:
   - `dashboard/server.py`: device-login bearers are linked to their device;
     `_purge_device_bearers()` removes matching in-memory bearers;
     revoke-all purges legacy in-memory bearers too.
   - `core/accounts/sessions.py`: manager device listing retains the
     server-side `session_key`; `revoke_all_devices()` cascades to
     `device_id`-linked sessions.
   - `dashboard/api_v1.py`: device DELETE purges associated in-memory bearers.
   - Public device facade still strips channel keys from responses.

## 4. Subprocess / shell boundary

- `actions/computer_control.py`: LLM-influenced window titles are sanitised
  before interpolation into PowerShell/AppleScript. Verified: ordinary letters
  (including `r`/`n`) survive; quotes, backticks, `$`, CR, LF are removed.
- `actions/open_app.py`: removed Windows `shell=True`; executable launches
  use argv form. URL/protocol/path launches use `os.startfile`, not
  `cmd /c start` (cmd would still parse metacharacters).
- Action loader isolates per-module load failures: a module whose optional
  dependency (e.g. Playwright in `actions/browser_control.py`) is missing
  fails as a recorded load error, never as a crash.

## 5. Tool permission boundary (`code_helper` gap — fixed)

`code_helper` was classified `READ_ONLY`, but its `run` and `build`
sub-actions execute code via subprocess — a genuine classification gap.
`auto` can be LLM-routed into an executing sub-action. Fixed in
`core/permissions.py`: `run`, `build`, and `auto` now escalate to
`USER_CONFIRMATION`; `explain` stays `READ_ONLY`. Covered by three regression
tests.

## 6. Residual risks (documented, not hidden)

- LAN dashboard defaults to **HTTP** (same-network convenience); TLS is
  opt-in via the generated cert pair. Don't expose the port to the internet.
- The legacy plaintext `/api/command` path stays for old clients.
- Legacy AES salt is fixed (`_AES_SALT = b'JARVIS-DASHBOARD-v1'`) for backward
  compatibility with old pairings.
- Boot secret migration is intentionally incomplete (see `docs/MIGRATION.md`).
- Multi-user accounts exist, but there is **no password/OIDC login** —
  sign-in is local PIN/QR pairing.
- Gemini Live usage and provider token counts are **not fully metered**.
- Windows-only runtime paths (audio, PyQt rendering, hotkeys, firewall) are
  statically reviewed only — see `docs/WINDOWS_QA.md`.
- Real AI providers and real audio hardware are exercised manually only;
  automated tests use mocks/fakes.
