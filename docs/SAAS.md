# SHIRAZI SaaS Foundation — Phase 8 (2026-09-23)

This document describes the multi-user SaaS backend introduced in Phase 8:
accounts, sessions, devices, provider policy, per-user permissions, usage
metering, and the encrypted secret store. It is the companion to
`docs/API.md` (endpoint reference) and `docs/SECURITY_NOTES.md` (threat
model and honest limitations).

**Headline rule, unchanged:** the single-user desktop flow must never
break. Every Phase 8 change keeps the old paths working — the account
system layers on top of the existing PIN/device-token dashboard auth, and
a `local` admin user is auto-created so the desktop behaves exactly as
before.

**FREE-FIRST rule:** every tier of the provider chain must contain a
free/local rung. Paid providers require explicit per-user opt-in and are
off by default. Nothing in Phase 8 spends money or assumes a paid key.

## 1. Architecture

```
core/accounts/
    models.py       dataclasses: User, Session, Device, usage + provider summaries
    store.py        multi-user SQLite store (config/shirazi_accounts.db)
    sessions.py     token lifecycle: create/adopt/validate/expire/revoke/revoke-all
    devices.py      device register/list/revoke facade over the store
    providers.py    per-user provider chain policy + FREE-FIRST enforcement
    permissions.py  per-user permission overrides (never looser than global)
    usage.py        per-user/provider counters + reports
    secrets.py      SecretStore: env → OS keyring → AES-256-GCM file; migration
dashboard/api_v1.py REST surface for the above (/api/v1/*)
```

The store is SQLite at `config/shirazi_accounts.db` (git-ignored). If the
database cannot be opened (disk full, permissions), the dashboard logs a
warning and runs in the old single-user in-memory mode — degraded, not
dead.

`dashboard/server.py` initialises `AccountStore`, `SessionManager`, the
`local` admin user, and the usage tracker **before** building the FastAPI
app, then mounts `dashboard/api_v1.py`. For tests, an `AccountStore` can
be injected (`DashboardServer(account_store=...)`).

## 2. Accounts

- `AccountStore.ensure_local_user()` creates (idempotently) the `local`
  admin account on first boot — the desktop's identity. Single-user
  installs never need to think about accounts.
- Users have roles (`admin` / `user`), a profile (display name, language),
  and preferences (JSON). Password/OIDC login is **not** implemented in
  Phase 8 — the `local` admin plus the existing PIN/device pairing remain
  the authentication story (see §8 Limitations).
- Per-user data: conversation history (`add_history`/`get_history`),
  automation rules (`save_rule`/`get_rules`/`delete_rule`) with JSON
  condition/action payloads, provider configs, and permission overrides.

## 3. Sessions (`core/accounts/sessions.py`)

- Tokens are `secrets.token_urlsafe(32)`; **only SHA-256 hashes are stored**
  (`_hash()`), raw tokens are never persisted.
- `create(user_id)` → bearer token. `adopt(raw_token, user_id)` imports an
  existing token (used for the dashboard's legacy PIN-issued bearers and
  QR device tokens) into the session mechanism without invalidating it.
- `validate(raw)` returns the `Session` or `None`; expired sessions
  (default TTL 30 days) are rejected and lazily purged.
- `revoke(raw)`, `revoke_all(user_id)`. Device-minted sessions carry a
  `device_id`; revoking the device cascades to its sessions
  (`revoke_device(user_id, device_id)`).
- The dashboard auth path now accepts **either** a legacy in-memory token
  **or** a validated general session — old clients keep working, new
  clients get the managed lifecycle.

## 4. Devices (`core/accounts/devices.py`)

Thin facade over the store: `register(user_id, label, platform)` →
`device_id` + one-time `device_token`; `login(device_id, device_token)`
validates and mints a device-bound session; `revoke` cascades.
`dashboard/server.py` persists QR-paired device tokens into this registry,
and `/api/device-login` checks the legacy in-memory map first, then the
persistent registry — so already-paired phones survive a server restart.

## 5. Providers (`core/accounts/providers.py`)

Per-user provider chain with admin gating:

- `get_chain / set_chain` — ordered provider names; `set_primary`.
- `set_enabled(user, provider, enabled, actor)` — admin-only; enabling a
  provider with no implementation (`PROVIDER_KEY_CONFIGS` reserves
  `openai`/`anthropic`/`groq`/`deepseek` slots) is refused.
- **FREE-FIRST enforcement** (`_check_free_first`): the chain must always
  contain at least one free/local rung (`gemini` free tier, `ollama`,
  `local`). You cannot disable/remove the last free rung, and you cannot
  set a chain with no free rung.
- **Paid opt-in**: `set_paid_opt_in(user_id, True, actor)` (admin-only)
  records explicit consent. A paid provider cannot be enabled or set as
  primary without it. Paid is off by default for every user.
- Keys are **never** accepted by the provider-config endpoints and never
  returned: `GET /api/v1/providers` reports only `key_configured: bool`.
  Key writes go through `providers.set_key()` → the encrypted
  `SecretStore` (user-namespaced: `user:<id>:provider:<p>:<key_name>`),
  never the account DB. Any `api_key`/`token` field smuggled into a
  config payload is rejected with 400.

`core/providers/registry.py` gained a usage-recorder hook and
`generate(..., user_id="local")`: every generation attempt records a
request/error count for that user+provider without changing generation
behaviour (recording failures are swallowed).

## 6. Permissions (`core/accounts/permissions.py`)

Per-user overrides layered over the global `core.permissions` engine:

- `set_override(store, user_id, tool, level, actor)` — admin-only.
- Overrides may only **tighten**, never loosen: requesting a level below
  the tool's global risk level raises `ValueError`.
- `effective_level(store, user_id, tool)` and `is_allowed(...)` resolve
  override → global → default-deny for unknown tools.

## 7. Usage (`core/accounts/usage.py`)

SQLite counters per user+provider: requests, errors, tool calls, and
provider-reported tokens. `report(user_id)` returns per-provider rows
plus a `notes` field stating the honest limits:

- Built-in providers do not report token counts, so `tokens` is 0 unless
  a provider starts reporting them.
- Gemini Live (voice) sessions are **not** counted.

## 8. Secrets (`core/accounts/secrets.py`)

Resolution order for every key (also used by `core/gemini.py`,
`core/providers/openrouter.py`, `memory/config_manager.get_gemini_key`):

1. Environment variable (e.g. `GEMINI_API_KEY`)
2. `SecretStore`: OS keyring when usable, else AES-256-GCM encrypted
   file (`config/credentials.enc`, data key in `config/.secret_key`,
   chmod 600). `set()` fails closed if no secure backend exists.
3. Legacy plaintext `config/api_keys.json` (read-only; one-time stderr
   note pointing at migration).

`migrate_api_keys_json()` is idempotent and backup-first: it moves
secret-looking keys (`api_key`/`_token`/`_secret`/`_password` suffixes +
`plugin_config`) into the store, backs up the original
(`api_keys.json.bak`, timestamped on later runs), stamps
`_secrets_migrated=true`, and leaves non-secret preferences in place.
`audit_plaintext_residue()` verifies no secret-looking keys remain and no
secret *value* appears anywhere in the file.

### Deferred: boot migration

Boot auto-migration is **intentionally not wired in Phase 8**. Twelve
modules read secrets directly from `config/api_keys.json` with no
fallback chain — `actions/web_search.py`, `dev_agent.py`, `code_helper.py`,
`computer_control.py`, `computer_settings.py`, `desktop.py`,
`file_processor.py`, `flight_finder.py`, `screen_processor.py`,
`youtube_video.py`, `send_message.py` (prefs only), and
`core/llm_client.py` (prefs only). Removing keys at boot would break the
desktop flow (e.g. `web_search._get_api_key()` does
`json.load(f)["gemini_api_key"]` — a `KeyError` after migration).

The migration machinery is implemented, tested (idempotent,
backup-first, fail-closed), and available explicitly via
`core.accounts.secrets.migrate_api_keys_json()` and
`memory.config_manager.ensure_migrated()`. Boot wiring lands only after
those readers are converted to `secrets.resolve_key()`. Until then the
central paths (`core/gemini.py`, `core/providers/openrouter.py`,
`config_manager.get_gemini_key()`) resolve env → encrypted store →
legacy file, so behaviour is unchanged and migration-ready.

## 9. Compatibility keep-list (Phase 9 must protect)

`hey_jarvis` · `~/.jarvis_profiles` · `~/.jarvis/reminders` ·
`jarvis_device_token` · `shutdown_jarvis` · `JarvisLive` ·
`_AES_SALT = b'JARVIS-DASHBOARD-v1'` · `jarvis_token → shirazi_token`
browser-storage migration. All preserved verbatim in Phase 8.

## 10. Limitations (honest, by design)

- **No password/OIDC login yet.** Multi-user storage exists; remote user
  login does not. The `local` admin + PIN/device pairing remain the auth
  story for now.
- **PIN flow retained** for local desktop↔phone pairing (low entropy,
  rate-limited, 600 s TTL) — kept for compatibility, not as a security
  boundary for multi-user.
- **Fixed AES salt** (`JARVIS-DASHBOARD-v1`) retained for the dashboard
  message crypto — compatibility over strength, documented in
  `docs/SECURITY_NOTES.md`.
- **Plaintext `text` fallback** on `/api/command` retained for legacy
  clients (type/length-hardened in Phase 7).
- **HTTP-on-LAN default**; HTTPS via self-signed cert when available
  (`serve()` generates/reuses `config/certs/shirazi.crt`). Manual trust
  steps for phones are in `docs/WINDOWS_QA.md`.
- Token counts in usage are provider-reported only (currently zero for
  built-ins); Live voice sessions uncounted.

## 11. Tests

`tests/test_phase8_saas.py` — 64 tests covering accounts, sessions
(create/validate/expire/revoke/adopt, hash-only storage), devices +
revoke cascade, provider FREE-FIRST/admin/paid-opt-in policy, per-user
permissions (cannot loosen), usage + registry hook, encrypted store
(real AES-GCM, no residue), migration idempotency, dashboard `/api/v1`
(auth gating, key-smuggling rejection, legacy `/api/command`
compatibility, logout revocation). Full suite: **253 tests, all passing**
(189 Phase-7 baseline + 64 new).
