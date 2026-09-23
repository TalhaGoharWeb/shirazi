# Security Notes — Shirazi repo (as of Phase 7, 2026-09-23)

This file documents the CURRENT secret-handling state of the codebase. It is a
record, not a fix list — the migration work is scheduled for later phases.

## Current state (Phase 2)

| Secret | Where it lives | Protection today |
|---|---|---|
| Gemini API key (+ per-plugin tokens) | `config/api_keys.json` | **Plaintext JSON.** Git-ignored (root `.gitignore` + `config/.gitignore`) but readable by any process/user on the machine. No encryption anywhere. |
| Dashboard session PIN | In-memory `DashboardServer._pending_keys` (6 chars, 600 s TTL) | One-time use, expires. **Phase 2 added attempt rate-limiting** (see below). |
| Dashboard bearer/device tokens | In-memory on server; browser `sessionStorage`/`localStorage` on phone | `secrets.token_urlsafe(32)`; revocable via `/api/revoke-devices`; lost on restart. |
| Dashboard message crypto | AES-256-CBC, key = SHA-256(PIN ‖ fixed salt `JARVIS-DASHBOARD-v1`) | Fixed salt; no PBKDF2. Plaintext `text` payload also accepted on `/api/command` and `/ws`. |
| Dashboard TLS | `config/certs/shirazi.key` + `shirazi.crt` (self-signed, chmod 600) | Git-ignored. HTTPS optional; default is plain HTTP on LAN. |
| OAuth tokens | `**/token*.json`, `**/client_secret*.json` | Git-ignored patterns; no such plugin ships in this repo. |

**Key exposure note (verified statically):** the Gemini API key never reaches the
browser — `dashboard/server.py::_get_gemini_key()` has zero callers and phone
audio is relayed server-side as PCM into the desktop Live session. The weak
points are: the low-entropy PIN, the fixed AES salt, plaintext `text` fallback,
and plain-HTTP default.

## What changed in Phase 2

1. Added `config/.gitignore` as defense-in-depth (root `.gitignore` already
   ignored `config/api_keys.json`, `config/certs/`, `config/whatsapp_web/`,
   `**/token*.json`, `**/client_secret*.json`, `.env*` — this local guard
   survives root-.gitignore edits or directory moves).
2. Added PIN attempt rate-limiting to `dashboard/server.py` (bounded attempts
   with cooldown; see `_pin_attempts` / `_check_pin_rate_limit`).
3. Removed unused plugin-extra deps (`google-api-python-client`,
   `google-auth-oauthlib`, `tinytuya`, `paho-mqtt`) so fewer credential-bearing
   packages sit on the install surface.

## What changed in Phase 7

1. **`/ws/cmd` command channel** (new): every message type is allowlisted
   and validated before dispatch — `dpad` keys restricted to
   `up/down/left/right/enter/space/esc/tab/F11`, `touchpad_click` buttons to
   `left|right`, `scroll` directions to the 4-way enum with amount clamped
   1–20, `volume` actions to `up/down/mute/unmute/set` with level clamped
   0–100. Tool names dispatched are hardcoded server-side; the client can
   never name a tool. Unknown message types are rejected.
2. **Permission engine re-check on every remote tool call**
   (`_run_remote_tool`): unknown tools fail closed, PRIVILEGED tools are
   denied outright. The physical tap on a PIN-paired phone counts as the
   human approval for USER_CONFIRMATION-level remote-control tools; free
   text through `/api/agent` keeps explicit per-step Approve/Deny.
3. **Per-call confirm-hook isolation** (`core/agent/executor.py`): the
   dashboard passes its phone-confirmation hook per call instead of
   mutating the shared agent — the desktop Live loop's hook cannot be
   hijacked from the dashboard path.
4. **Origin checks** added to `/ws`, `/ws/phone-audio`, and `/ws/cmd`
   (same-host origins and non-browser clients pass; token auth remains
   the real gate).
5. **Rate limiting:** `/api/command` + `/ws/cmd` at 120 msgs/60 s per
   token (429 beyond); touchpad moves at 40/s (excess dropped, never
   queued); `/api/agent` text capped at 2000 chars; legacy command text
   capped at 1000 chars and strictly type-checked (must be a string).
6. **Plaintext `text` fallback retained but hardened** for old clients:
   strict type + length validation on `/api/command` and the `/ws`
   command handler; AES-256-CBC `enc` payloads remain the preferred path.

Still deferred to Phase 8 (unchanged from the plan above): replacing the
6-char PIN flow with multi-user auth, rotating/fixing the AES salt
derivation, dropping the plaintext fallback entirely, TLS-by-default.

## What changed in Phase 8

1. **Encrypted secret store** (`core/accounts/secrets.py`): resolution
   order is now env → OS keyring (when usable) → AES-256-GCM encrypted
   file (`config/credentials.enc`, data key in `config/.secret_key`,
   chmod 600) → legacy plaintext `config/api_keys.json` (read-only,
   one-time stderr note). `set()` fails closed when no secure backend
   exists. Key names (never values) appear in logs/reports.
2. **Central key paths migrated**: `core/gemini.py`, 
   `core/providers/openrouter.py`, and
   `memory/config_manager.get_gemini_key()` resolve through the same
   chain. Behaviour is unchanged when no encrypted store exists (falls
   through to the legacy file).
3. **Migration implemented but NOT auto-run at boot** (deliberate):
   `migrate_api_keys_json()` is idempotent, backup-first
   (`api_keys.json.bak`, timestamped on later runs), fail-closed, and
   leaves non-secret preferences in place — but twelve modules still
   read secrets directly from `config/api_keys.json` with no fallback
   (`actions/web_search.py`, `dev_agent.py`, `code_helper.py`,
   `computer_control.py`, `computer_settings.py`, `desktop.py`,
   `file_processor.py`, `flight_finder.py`, `screen_processor.py`,
   `youtube_video.py`, plus prefs-only reads in `send_message.py` /
   `core/llm_client.py`). Removing keys at boot would break the desktop
   flow, so boot wiring waits until those readers move to
   `secrets.resolve_key()`. See `docs/SAAS.md` §8.
4. **Session tokens stored hashed**: `/api/v1` sessions persist only
   SHA-256 hashes; raw bearer/device tokens are never in the DB.
   API responses show at most a 12-char hash prefix.
5. **Keys excluded from the API by design**: `/api/v1/providers*`
   reject `api_key`/`key`/`secret`/`token` fields with 400 and report
   only `key_configured: bool`. Device tokens are returned once at
   registration.
6. **Per-user permission overrides** (`core/accounts/permissions.py`)
   can only tighten, never loosen, the global `core.permissions` risk
   levels.

Still deferred after Phase 8: password/OIDC login (multi-user storage
exists; remote login does not), replacing the 6-char PIN pairing flow,
rotating/fixing the AES salt derivation, dropping the plaintext `text`
fallback entirely, TLS-by-default, and boot auto-migration (blocked on
item 3 above).

## Planned (do NOT implement before the scheduled phases)

- **Phase 3/4:** migrate `config/api_keys.json` to an encrypted store (OS
  keyring or encrypted file) — the plaintext-JSON format must not be
  proliferated into new features (rebrand provider abstraction, SaaS auth).
- **Phase 7/8:** full dashboard auth overhaul — replace the 6-char PIN flow
  with real multi-user auth, rotate/fix the AES salt derivation, drop the
  plaintext-`text` fallback, make TLS the default.

## Operator guidance (until then)

- Never commit `config/api_keys.json`, `config/certs/`, or any `token*.json`.
- Phase 3 renamed the dashboard TLS pair to `shirazi.key`/`shirazi.crt`. A legacy `jarvis.key`/`jarvis.crt` pair is *reused* when present (so paired phones keep trusting the same certificate); a fresh pair is generated only when neither exists. See `docs/LEGACY_COMPAT.md`.
- Never paste the API key or tokens into chat, logs, screenshots, or error
  reports. A QR pairing code on screen is a one-time 600-second secret — treat
  it as one.
- On a shared or managed machine, set restrictive file permissions on the
  `config/` directory (chmod 700) as a stopgap until encryption lands.
