# Legacy Compatibility — identifiers Shirazi keeps on purpose

**Phase 3 (rebrand), 2026-09-23.** The product is now **SHIRAZI**. Everything
user-facing was rebranded — except the identifiers below, which are kept
deliberately because renaming them would break real things: paired phones,
saved sessions, scheduled tasks, or a trained ML model. Each entry says what
it is, why it stays, and the path away from it.

Rule of thumb: a legacy identifier is kept only while it protects user data
or working integrations. None of them is user-facing branding.

---

## 1. `_AES_SALT = b'JARVIS-DASHBOARD-v1'` — dashboard crypto salt
- **Where:** `dashboard/server.py:84`, mirrored in `dashboard/static/app.html`.
- **Why it stays:** the salt feeds the SHA-256 key derivation for the
  phone↔desktop AES channel (key = SHA-256(PIN ‖ salt)). Changing the value
  would silently break key agreement with already-paired phones.
- **Migration path:** none needed — the *value* is a crypto constant, not
  branding. It is never displayed to the user. A future auth overhaul
  (Phase 7/8) may rotate the whole derivation, at which point the salt goes
  with it.

## 1b. `jarvis.key` / `jarvis.crt` — legacy dashboard TLS pair
- **Where:** `config/certs/`; resolved by `_cert_pair()` in
  `dashboard/server.py`.
- **Why it stays (as fallback):** a phone paired under Mark-LIV trusts the
  certificate it was shown. `_cert_pair()` prefers the Shirazi pair but
  reuses the legacy pair when the new one doesn't exist — already-paired
  phones keep connecting without a new certificate warning. Fresh installs
  generate `shirazi.key` / `shirazi.crt` (self-signed, one-time).
- **Migration path:** automatic; nothing for the user to do.

## 2. `WAKE_MODEL = "hey_jarvis"` — wake-word ONNX model id
- **Where:** `core/wake_word.py`.
- **Why it stays:** this is the *pretrained model's internal label*. A wake
  model is trained on one exact phrase ("Hey Jarvis"); renaming the string
  does not rename what the model hears — only retraining does that, and no
  Hey-Shirazi model exists yet.
- **What Shirazi does instead:** the branded target phrases live in config
  (`wake_words: ["hey_shirazi", "shirazi"]`, see `config/settings.json` and
  `memory/config_manager.get_wake_words()`), resolved through
  `WAKE_WORD_ALIASES` in `core/wake_word.py`. Until a Hey-Shirazi ONNX model
  ships, every alias falls back to the legacy model, and
  `effective_listening_phrase()` reports the phrase the detector *actually*
  hears ("Hey Jarvis") — every user-facing string uses that helper, so the
  user is never told to say something the model cannot hear.
- **Migration path:** package/train a Hey-Shirazi openwakeword model, point
  an alias at it, and `effective_listening_phrase()` flips honestly.

## 3. `shutdown_jarvis` — legacy tool name (alias)
- **Where:** `main.py` `_execute_tool` accepts both `shutdown_shirazi` and
  `shutdown_jarvis`; the Live tool declaration now advertises only
  `shutdown_shirazi`; `core/prompt.txt` references `shutdown_shirazi`.
- **Why it stays:** old automations, saved sessions, and muscle memory may
  still call the old name. The alias costs one tuple and breaks nothing.
- **Migration path:** none required; the alias is permanent and harmless.

## 3b. `JarvisLive` / `JarvisUI` — legacy class aliases
- **Where:** end of `main.py` (`JarvisLive = ShiraziLive`), end of `ui.py`
  (`JarvisUI = ShiraziUI`).
- **Why they stay:** user scripts written for the Mark-LIV era may do
  `from main import JarvisLive`. One alias line each, permanent and harmless.

## 4. Dashboard browser storage keys `jarvis_token` / `jarvis_key` / `jarvis_device_token`
- **Where:** `dashboard/static/app.html`, `dashboard/static/login.html`.
- **Why they stay (as fallback):** these live in the *phone's* browser
  storage. A phone paired under Mark-LIV still holds the legacy keys; after
  the upgrade the server issues `shirazi_*` keys, and the UI reads the new
  keys first, falling back to the legacy ones. On the next successful login
  the legacy device key is removed.
- **Migration path:** automatic — one fresh login moves the phone fully onto
  `shirazi_*` keys. Server-side validation is by token *value*, so no
  server change was needed.

## 5. `"speaker": "jarvis"` — legacy feed tag
- **Where:** accepted by `dashboard/static/app.html`'s `append()` alongside
  the new `"shirazi"` tag (server now sends `"shirazi"`).
- **Why it stays:** an old desktop serving a new phone page (or vice versa
  during a staggered upgrade) still renders the assistant's messages.
- **Migration path:** none required; the check is two string comparisons.

## 6. Autostart ids: `JARVIS_AI`, `com.jarvis.assistant.plist`, `jarvis.desktop`
- **Where:** `ui.py` `_check_autostart` / `_toggle_autostart`.
- **Why they stay (as recognized+cleaned):** new installs register
  `SHIRAZI_AI` / `com.shirazi.assistant.plist` / `shirazi.desktop`. The legacy
  ids are still *recognized* so an upgrade isn't misreported as "autostart
  off" while a legacy entry still launches the app — and they are *deleted*
  whenever autostart is toggled, so no stale Mark-LIV entry survives.
- **Migration path:** automatic on first autostart toggle.

## 7. Scheduled-task ids: `JARVIS_GameUpdater`, `com.jarvis.gameupdater.plist`, `# JARVIS_GameUpdater`
- **Where:** `actions/game_updater.py`.
- **Why they stay (as recognized+cleaned):** new schedules use
  `SHIRAZI_GameUpdater` / `com.shirazi.gameupdater.plist` /
  `# SHIRAZI_GameUpdater`. Cancel/status also match the legacy ids, and
  (re)scheduling deletes them — an upgrade never leaves a stale task behind.

## 8. Data directories: `~/.jarvis_profiles`, `~/.jarvis/reminders`, `~/Desktop/JarvisProjects`
- **Where:** `actions/browser_control.py`, `actions/reminder.py`,
  `actions/dev_agent.py`.
- **Why they stay (as fallback):** new installs use `~/.shirazi_profiles`,
  `~/.shirazi/reminders`, `~/Desktop/ShiraziProjects`. If the legacy
  directory exists and the new one doesn't, the legacy one is *reused* —
  browser automation cookies, reminder scripts, and dev projects survive the
  upgrade instead of being orphaned.
- **Migration path:** automatic; nothing for the user to do.

## 9. `config/jarvis.ico` — legacy icon file
- **Where:** `config/`; `ui.py` now prefers `config/shirazi.ico` (shipped
  from Phase 3) and falls back to the legacy file if the new one is missing.
- **Why it stays:** a pre-rebrand install directory keeps working.
- **Migration path:** none required; the new icon ships with the repo.

## 10. Legacy default assistant names `"JARVIS"` / `"J.A.R.V.I.S"`
- **Where:** `ui.py` subtitle logic; `core/migration.py`.
- **Why they stay (as recognized):** an upgraded `config/api_keys.json`
  may still carry the old default. The UI keeps showing the friendly
  subtitle/backronym for those values, and the migration layer rebrands an
  untouched default to `SHIRAZI` (a *custom* name is never touched).
- **Migration path:** automatic via `core/migration.py` on first boot.

## 11. Chat-log tag prefix `"jarvis:"`
- **Where:** `ui.py` log parser accepts `("shirazi:", "jarvis:")`.
- **Why it stays:** old log lines keep their assistant-tag colouring.

## 12. Historical records (intentionally untouched)
- **`SHIRAZI_MIGRATION_MAP.md`** — the Phase 1 audit of the upstream
  Mark-LIV tree. Renaming brand marks inside it would falsify the audit.
- **`archive/dead_code/`** — quarantined Mark-LIV-era modules (Phase 2).
  Kept byte-identical as a historical record; not part of the product.
- **`LICENSE`** — upstream project's license text; left as-is.
- **`core/face_model.obj`** — geometry only, never contained branding.
