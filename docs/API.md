# SHIRAZI Dashboard API — v1 (Phase 8)

Base path: `/api/v1`. All endpoints except `/api/v1/health` require a
bearer token: `Authorization: Bearer <token>`. Tokens are the dashboard's
existing PIN-issued bearers / device tokens (adopted into the Phase 8
session mechanism) or sessions minted via the account layer.

Auth failures: `401 {"error": "Unauthorized"}`. Admin-only endpoints add
`403 {"error": "Admin required"}`. If the account store is unavailable
(disk full etc.) the account-backed endpoints return
`503 {"error": "accounts layer unavailable"}` and the dashboard keeps
serving the legacy single-user routes.

**Keys are never part of this API.** Provider config endpoints accept
only `provider` / `enabled` / `chain` / `primary` / `opt_in`; any
`api_key` / `key` / `secret` / `token` field in a payload is rejected
with `400`. Key state is reported as `key_configured: bool` only. Key
writes happen exclusively through the encrypted `SecretStore`
(`core/accounts/secrets.py`), never the account DB, never over HTTP.

Legacy (pre-v1) routes — `/api/command`, `/api/device-login`, `/ws`,
`/ws/phone-audio`, `/ws/cmd`, `/api/agent`, PIN pairing — are unchanged;
see `docs/PHASE7.md` and `docs/LEGACY_COMPAT.md`.

## Health

`GET /api/v1/health` — no auth.
```json
{"ok": true, "api": "v1", "saas": "Phase 8 — SaaS foundation",
 "accounts": true, "sessions": true}
```

## Identity

`GET /api/v1/me`
```json
{"id": "local", "name": "Shirazi", "role": "admin",
 "voice": "Charon", "language": "en", "avatar": "default",
 "paid_opt_in": false, "prefs": {}}
```

## Users (admin)

`GET /api/v1/users` → `{"users": [{"id", "name", "role", "voice",
"language", "paid_opt_in", "last_seen"}]}`

`POST /api/v1/users` — body: `{"id", "name", "role": "standard"|"admin",
"voice", "language"}` → `200 {"ok": true, "id", "role"}`.
`400` for a missing id / bad role, `409` if the id exists.

## Sessions

`GET /api/v1/sessions` → `{"sessions": [{"token_prefix", "kind",
"device_id", "label", "created_at", "expires_at", "last_seen",
"revoked"}]}` — the caller's unrevoked sessions. Only a 12-char hash
prefix is shown; raw tokens are never recoverable here.

`POST /api/v1/sessions/revoke-all` → `{"ok": true, "revoked": N}` —
revokes every session of the caller **except** the current token.

`POST /api/v1/logout` → `{"ok": true}` — revokes the caller's current
token (both the session record and the legacy in-memory token set).

## Devices

`GET /api/v1/devices` → `{"devices": [{"id", "name", "kind",
"created_at", "last_seen", "revoked"}]}` — never includes token
material.

`POST /api/v1/devices` — body: `{"kind": "phone", "name": "Pixel"}` →
`200 {"ok": true, "device_id", "device_token", "kind"}`. The
`device_token` is shown **once**; it is stored hashed and can never be
recovered. Pair the device with it via the existing `/api/device-login`
(which checks the persistent registry after the legacy in-memory map).

`DELETE /api/v1/devices/{device_id}` → `{"ok": true}` — revokes the
device **and** every session minted from its token (cascade). `404`
`{"error": "unknown device"}` for a device that is not the caller's
(non-admin).

## Usage

`GET /api/v1/usage?days=30`
```json
{"user_id": "local", "days": 30,
 "providers": {"gemini": {"requests": 12, "tokens_in": 0,
                          "tokens_out": 0, "tool_calls": 3, "errors": 1}},
 "notes": ["requests = provider attempts via core/providers/registry.generate().",
           "tokens_in/tokens_out are 0: no provider implementation reports token counts today (not estimated).",
           "Voice (Gemini Live) sessions do not route through the registry and are not counted here."]}
```
`days` is clamped to 1–365.

## Providers

`GET /api/v1/providers`
```json
{"chain": ["gemini", "openrouter", "ollama"],
 "providers": [{"name": "gemini", "tier": "free", "enabled": true,
                "available": true, "reason": "",
                "key_configured": true, "capabilities": ["chat"]}],
 "free_first": "Free tiers/local are the defaults; paid providers need explicit opt-in and are never assumed."}
```
`key_configured` is a boolean only — never a key value.

`POST /api/v1/providers/config` (admin) — body forms:
- `{"provider": "openrouter", "enabled": true|false}`
- `{"provider": "gemini", "primary": true}`
- `{"chain": ["gemini", "ollama"]}` (replaces the whole chain)

→ `200 {"ok": true, "provider": "<name>", "chain": [...]}`.
Rules enforced (400/403 otherwise): admin-only; unknown providers
rejected; enabling a provider with no implementation rejected; the
chain must always keep a free/local rung (`gemini`/`ollama`/`local`);
a paid provider needs `paid_opt_in` first. **Any key-like field → 400.**

`POST /api/v1/providers/paid-opt-in` (admin) — body:
`{"opt_in": true}` → `200 {"ok": true, "paid_opt_in": true, "note":
"explicit opt-in recorded; free tiers remain the defaults"}`. Records
explicit consent for the calling admin's own account. Default: `false`;
paid providers stay disabled until this is set.

## Permissions

`GET /api/v1/permissions`
```json
{"user_id": "local", "role": "admin",
 "effective": {"shell_exec": "privileged", "...": "..."},
 "overrides": {"some_tool": "user_confirmation"}}
```
`effective` merges the global `core.permissions` levels with the user's
overrides (overrides can only tighten, never loosen). Override
management is code-level in Phase 8
(`core/accounts/permissions.py`, admin-only) — there is no REST writer
by design.
