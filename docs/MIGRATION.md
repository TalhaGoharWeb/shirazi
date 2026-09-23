# Migrating from Mark-LIV to Shirazi — user guide

**Phase 3, 2026-09-23.** Shirazi is the new name of the assistant formerly
known as Mark-LIV / JARVIS. This guide tells you what changed, what was
preserved, and what (if anything) you need to do.

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
├── settings.json      # non-secret defaults (committed; you can edit)
├── api_keys.json      # secrets + your prefs (git-ignored; written by setup screen)
├── providers.json     # AI provider registry skeleton (Phase 4 will wire it)
├── devices.json       # device registry skeleton (later phase)
├── shirazi.ico        # app icon (jarvis.ico kept as fallback)
├── certs/             # dashboard TLS pair (git-ignored)
└── backup/            # timestamped config backups from migration
```

Precedence: `api_keys.json` (yours) wins over `settings.json` (shipped
defaults). Full encrypted secret storage arrives in Phase 4 — until then,
the plaintext-JSON state documented in `docs/SECURITY_NOTES.md` is unchanged.

## Things to know after upgrading

- **Windows firewall:** the dashboard firewall rules are now named
  "SHIRAZI Dashboard …". The old "JARVIS Dashboard …" rules may linger in
  Windows Firewall — harmless, but you can delete them.
- **Scheduled game updates:** recreated under the new task name on next
  schedule; legacy tasks are removed automatically.
- **`.env`:** a `.env.example` template now ships at the repo root for the
  Phase 4 config work. Nothing reads `.env` yet — it's forward-looking.
- **No paid services were added.** The rebrand is free-first, like everything
  else: no new dependencies, no subscriptions.

## If something looks wrong

1. Check `config/backup/` — your pre-migration config is there, timestamped.
2. The migration report prints at startup (`[SHIRAZI] migration: …`) and in
   the app log.
3. Restoring is just copying the backup back over `config/api_keys.json`
   (the app will re-run migration on next boot — that's fine, it's
   idempotent).
