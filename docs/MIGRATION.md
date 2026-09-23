# Migrating from Mark-LIV to Shirazi — user guide

**Phases 4–8 landed, 2026-09-23** (provider abstraction, PyQt HUD, mobile
dashboard, multi-user accounts, SaaS foundations). This guide is updated for
the Phase 9 tree. Encrypted secret storage, the provider registry, and the
device registry all shipped — see below.

**Short version: just start the app.** The first boot detects your Mark-LIV
config, backs it up to `config/backup/`, migrates it, and tells you what
happened in the log. Nothing you need is deleted.

---

## What changed (user-visible)

| Area | Before | After |
|---|---|---|
| App / window / HUD title | JARVIS | **Shirazi** |
| Assistant name default | JARVIS | **SHIRAZI** (your custom name is kept — see below) |
| Assistant personality | Tony Stark's AI | **Shirazi**: an AI assistant — intelligent, respectful, calm, professional; concise or detailed as the moment needs; proactive, technically capable, culturally respectful, research-oriented |
| Shutdown tool | `shutdown_jarvis` | `shutdown_shirazi` (old name still works as an alias) |
| Phone dashboard | JARVIS Dashboard | **Shirazi** dashboard; token storage keys renamed to `shirazi_*` (old `jarvis_*` keys still accepted — see below) |
| Wake word (target) | "Hey Jarvis" | **"Hey Shirazi" / "Shirazi"** — configurable in settings (see the honest note below) |
| Autostart entries | `JARVIS_AI`, `com.jarvis.assistant`, `jarvis.desktop` | `SHIRAZI_AI`, `com.shirazi.assistant`, `shirazi.desktop` (legacy entries auto-removed on toggle) |
| App icon | `config/jarvis.ico` | `config/shirazi.ico` |
| Upload folder (phone) | `~/Downloads/JARVIS Uploads` | `~/Shirazi Uploads` (path: `Downloads`/`Documents` → `Shirazi Uploads`) |
| TLS cert files | `config/certs/jarvis.key` / `jarvis.crt` | `config/certs/shirazi.key` / `shirazi.crt` (legacy pair reused when present; fresh pair generated only if neither exists) |
| Browser automation profiles | `~/.jarvis_profiles` | `~/.shirazi_profiles` (old dir reused if present) |
| Reminder scripts | `~/.jarvis/reminders` | `~/.shirazi/reminders` (old dir reused if present) |
| Dev projects | `~/Desktop/JarvisProjects` | `~/Desktop/ShiraziProjects` (old dir reused if present) |

## What was preserved

- **Your API key, voice choice, devices, plugin settings, memory, and every
  other setting** — migrated verbatim. The migration backs up your config to
  `config/backup/api_keys.<timestamp>.json` *before* changing anything, then
  validates that no key was lost.
- **Your custom assistant name.** If you renamed the assistant to anything
  other than the old default, it is kept exactly as-is. Only an untouched
  default ("JARVIS") becomes "SHIRAZI".
- **Your phone pairing.** A phone paired under Mark-LIV keeps working: the
  app reads the new `shirazi_*` storage keys first and falls back to your
  existing `jarvis_*` keys. Log in once and the phone moves fully onto the
  new keys (the legacy device key is cleaned up automatically).
- **Old configs stay readable.** The on-disk format didn't change — a config
  from any era loads fine, indefinitely. The `brand` marker just records
  which era wrote it.

## The honest wake-word note

The **target** wake phrases are now "Hey Shirazi" and "Shirazi", and they're
configurable (`wake_words` in `config/settings.json`, or the settings UI).
But wake-word models are trained on one exact phrase, and the only
pretrained model available today listens for the legacy phrase **"Hey
Jarvis"** — no config alias can make it hear "Shirazi" until a Hey-Shirazi
model is trained and packaged. So today, the detector honestly listens for
"Hey Jarvis"; the app tells you so (hover the WAKE WORD button). Detection
is fully local — mic audio is never streamed to the cloud for wake-word
detection. This is a documented limitation, not a bug:
`docs/LEGACY_COMPAT.md`.

## Config layout (new in Phase 3)

```
config/
├── settings.json        # non-secret defaults (committed; you can edit)
├── api_keys.json        # secrets + your prefs (git-ignored; written by setup screen)
├── providers.json       # AI provider registry — WIRED (Phase 4): Gemini,
│                        #   Gemini Live, OpenRouter, Ollama with chain failover
├── devices.json         # device registry — WIRED (Phase 7): paired phones
│                        #   with hashed tokens and per-device bearer sessions
├── shirazi_memory.db    # encrypted layered memory (SQLite, git-ignored)
├── shirazi.ico          # app icon (jarvis.ico kept as fallback)
├── certs/               # dashboard TLS pair (git-ignored)
└── backup/              # timestamped config backups from migration
```

Precedence: `api_keys.json` (yours) wins over `settings.json` (shipped
defaults).

**Encrypted secret storage (resolved in Phase 7).** Secrets now live in
`core/accounts/secrets.py`: the OS credential store (`keyring` — Windows
Credential Manager / macOS Keychain / Secret Service) is tried first, with an
encrypted file store as the fallback. Nothing secret is written to plaintext
JSON anymore. One deliberate gap remains: **boot secret migration is
incomplete** — a secret that exists only in the old plaintext location is not
auto-migrated on first boot; re-enter it once in ⚙ → Providers (or let the
provider's first use prompt you) and it lands in the encrypted store. The old
plaintext-JSON state is documented in `docs/SECURITY_NOTES.md` for history.

## Phases 4–8: what else changed since the rebrand

- **Phase 4 — AI provider abstraction.** `core/providers/`: Gemini, Gemini
  Live (realtime audio), OpenRouter, Ollama. Configurable chain with
  free-first ordering; a failing/quota-hit provider fails over automatically.
- **Phase 5 — Desktop HUD.** PyQt6 interface with holographic avatar
  (software-rendered, real lip-sync), reactor, push-to-talk, permission
  prompts, i18n (en / اردو / العربية / Roman Urdu).
- **Phase 6 — Mobile dashboard.** FastAPI + WebSocket PWA (`dashboard/`):
  PIN/QR pairing, tap-to-talk, D-pad/touchpad, volume, telemetry, files.
  Phase 7's auth work is covered by `docs/API.md`.
- **Phase 7 — Multi-user + permissions.** Accounts, sessions, device
  registry with revocation that cascades to every session a device minted
  (hardened in Phase 9 — see `docs/SECURITY_AUDIT.md`). The permission gate
  (`core/permissions.py`) now classifies every tool call.
- **Phase 8 — SaaS foundations.** Per-user provider chains, usage metering
  (local-first; Gemini Live and token counts not yet fully metered —
  `docs/SAAS.md`).

**Rollback:** migration is idempotent — restoring `config/backup/*.json`
over `config/api_keys.json` (or deleting `config/` for a full reset) and
restarting is always safe; the app re-runs migration on next boot.

**Preserved across all phases:** your API keys, voice choice, devices, plugin
settings, memory, custom assistant name, phone pairing, old autostart/task
names (auto-removed on toggle), legacy TLS cert pair, legacy storage dirs
(`~/.jarvis_profiles`, `~/.jarvis/reminders`, `~/Desktop/JarvisProjects`) —
see `docs/LEGACY_COMPAT.md` for the full compatibility contract.

## Things to know after upgrading

- **Windows firewall:** the dashboard firewall rules are now named
  "SHIRAZI Dashboard …". The old "JARVIS Dashboard …" rules may linger in
  Windows Firewall — harmless, but you can delete them.
- **Scheduled game updates:** recreated under the new task name on next
  schedule; legacy tasks are removed automatically.
- **`.env`:** a `.env.example` template ships at the repo root for future
  config work. Nothing reads `.env` yet — it's still forward-looking.
- **No paid services were added.** The rebrand is free-first, like everything
  else: no new dependencies, no subscriptions.

## If something looks wrong

1. Check `config/backup/` — your pre-migration config is there, timestamped.
2. The migration report prints at startup (`[SHIRAZI] migration: …`) and in
   the app log.
3. Restoring is just copying the backup back over `config/api_keys.json`
   (the app will re-run migration on next boot — that's fine, it's
   idempotent).
