# SHIRAZI Migration Map — Phase 1 Repository Audit

**Audited:** 2026-09-23 · **Working copy:** `~/workspace/shirazi` (clone of `https://github.com/FatihMakes/Mark-LIV`) · **Upstream untouched**
**Git:** branch `main`, 1 commit (`476a9c0` "Add files via upload"), working tree clean.
**Scale:** 58 files, 48 Python modules, **24,843 lines of Python** (verified via `wc -l`). All 48 modules pass `py_compile` (zero syntax failures — static only, no runtime import test yet).
**Audit mode:** read-only. No code files were modified. Windows-only behavior is marked **UNVERIFIED** (audit ran on Linux; user's real environment is Windows).

> **Coordinator note on requirement cross-references:** the numbered list of the mission's 52 requirements was not in this auditor's context. §11 maps every component to the mission *workstreams* named in the task (rebrand, AI provider abstraction, tool+permission engine, PyQt6 UI + avatar/reactor/lip-sync, FastAPI mobile dashboard incl. Siri-like tap-to-talk, i18n en/ur/ar + Roman Urdu, research mode, layered memory, SaaS foundation) under labels R1–R9. The coordinator should pin these to exact requirement numbers.

---

## 1. Architecture overview

A single-process desktop voice assistant. One Python process runs **three cooperating loops**:

1. **Qt event loop (main thread)** — `ui.py` `MainWindow`/`JarvisUI`: the HUD, settings drawer, overlays, confirm banner, QR pairing. Started via `ui.root.mainloop()` in `main()`.
2. **asyncio loop (background thread)** — `JarvisLive.run()` in `main.py`: the Gemini Live websocket session, mic streaming, tool-call dispatch, phone-audio relay, dashboard serving.
3. **Plugin/action executor threads** — tool handlers run in `run_in_executor` / `asyncio.to_thread` so the Live session never blocks on a slow tool.

```
                    ┌──────────────────────────────────────────────────┐
                    │  main.py :: JarvisLive (asyncio, background thr) │
                    │  • Gemini Live session (LIVE_MODEL)              │
                    │  • mic PCM → send_realtime_input(audio=...)      │
                    │  • _execute_tool(): 8 inline + 16 actions        │
                    │    + N plugins, via ActionRegistry/PluginRegistry│
                    │  • DashboardServer (port 8000)                   │
                    └───────┬──────────────┬──────────────┬────────────┘
                            │              │              │
                 sounddevice│   PyQt6 sig/slot│  asyncio.Queue│
                            ▼              ▼              ▼
                    ┌──────────────────────────────────────────────────┐
                    │  ui.py :: JarvisUI / MainWindow (Qt main thread) │
                    │  HudCanvas: avatar head (core/avatar.py) or      │
                    │  reactor core; visemes via push_visemes()        │
                    └──────────────────────────────────────────────────┘
```

**Conversation engine:** Google Gemini Live API (`google-genai` SDK ≥2.8). Audio in/out is native: mic PCM streams up, model audio streams down and is played through `sounddevice`. The system prompt (`core/prompt.txt` + runtime-injected identity/capabilities/limits) is assembled per session in `JarvisLive._build_config()` (main.py:952).

**Side-call engine:** `core/gemini.py` — one-shot REST/Live "ladder" (`LIVE → gemini-2.5-flash-lite → gemini-2.5-flash`, etc.) with timeouts, 429-cooldowns and a 3-slot semaphore so side calls never starve the live conversation.

**Local-LLM engine (legacy/alternate):** `core/llm_client.py` — Ollama / OpenAI-compatible chat with tool calling and sentence-streaming for TTS. Header says "MARK XL"; used by `code_helper`/`dev_agent` paths (`call_llm_text`), not by the Live loop.

**Memory:** flat JSON file `memory/long_term.json` (6 categories: identity, preferences, projects, relationships, wishes, notes) + `save_memory`/`recall_memory` tools + session summaries. No SQLite.

---

## 2. Entry points

| File | Lines | Role |
|---|---|---|
| `main.py` | 2283 | **Primary entry.** `main()` (line 2269): builds `JarvisUI("face.png")`, waits for API key via UI, starts `JarvisLive` on a daemon thread, runs Qt mainloop. Owns: Live session lifecycle, tool dispatch (`_execute_tool`, line 1113), mic/audio pipelines, wake-word gating, push-to-talk, phone-audio relay, dashboard hosting, reconnect logic. |
| `ui.py` | 5437 | **All UI.** `JarvisUI` facade (line 5216) over `MainWindow` (line 2919). HUD, avatar canvas, settings drawer, overlays, QR pairing overlay, confirm banner, memory panel, plugin manager, clipboard panel, theming. |
| `setup.py` | 129 | **Installer.** Python 3.11–3.13 gate, `pip install -r requirements.txt`, Playwright browser install (best-effort), `face_model.obj` asset check, per-OS post-install notes. |
| `dashboard/server.py` | ~886 | **FastAPI phone dashboard** (port 8000, optional HTTPS 8001). Started from `JarvisLive.run()` (main.py:2069). |
| `requirements.txt` | 92 | Deps with `sys_platform` markers; Windows-only set is auto-filtered on other OSes. |

`main.py` key constants: `LIVE_MODEL = "models/gemini-3.1-flash-live-preview"` (line 99). `API_CONFIG_PATH` → `config/api_keys.json`.

---

## 3. Module-by-module inventory

### 3.1 `core/` (17 modules, 5,852 lines)

| Module | Lines | What it does | Phase relevance |
|---|---|---|---|
| `action_loader.py` | 221 | Discovers `actions/*.py` via `TOOL` dict; validates name/description/parameters/handler; `ActionRegistry` with `get_tool_declarations()`, `run()`, `has()`, `scheduling()`. Fault-isolated: bad files are skipped, never abort the scan. | Tool engine foundation. Keep; extend for permission tiers. |
| `plugin_loader.py` | 285 | Same pattern for `plugins/*.py` via `PLUGIN` dict + `run(parameters, player, session_memory)`; plugin settings schemas (`PLUGIN_SETTINGS`) feed the UI's plugin-settings overlay. Opt-out enable model (`plugins_enabled` in config). | Tool engine foundation. Keep. |
| `llm_client.py` | 586 | Ollama + OpenAI-compatible client: `call_llm`, `call_llm_stream` (SSE sentence streaming), `call_llm_text`, `warmup_model`, `ensure_ollama_running`. Config keys: `llm_provider`, `llm_url`, `llm_model`. | **AI provider abstraction starts here.** Header says "MARK XL" (rebrand). |
| `gemini.py` | 439 | One-shot Gemini ladder (`FAST`/`SMART`/`SEARCH` tiers; Live-first, then pinned REST, then `-latest` aliases). Key cached from `config/api_keys.json`. `call()`, `text()`, `as_json()`. | Provider abstraction: centralize all Gemini REST here. Model names are pinned 2.5-era — verify currency in Phase 2. |
| `wake_word.py` | 211 | Opt-in local wake word via **openwakeword** (`WAKE_MODEL = "hey_jarvis"`, ONNX, threshold 0.5, 16 kHz). Lazy import; `install_and_download()` pip-installs + downloads models from UI. Mic callback does a non-blocking queue push only. | Rebrand: pretrained phrase is "Hey Jarvis" — **no "Hey Shirazi" model ships**; needs new wake strategy. |
| `hotkey.py` | 173 | Push-to-talk (default chord Ctrl+Space). **Windows:** global via `GetAsyncKeyState` polling @30 Hz (needs no message loop, reports press+release). **macOS/Linux:** window-scoped Qt shortcut only (`scope` reports which). | Hotkey/global-key handling. UNVERIFIED on Windows at runtime. |
| `stt.py` | 93 | Whisper (`faster-whisper`) + Vosk offline STT classes. | **DEAD CODE — zero importers.** Legacy from MARK XL. Candidate for removal/quarantine in Phase 2. |
| `tts.py` | 442 | EdgeTTS / Kokoro / ElevenLabs engines + `TTSPlayer`. | **DEAD CODE — zero importers.** Legacy from MARK XL. `miniaudio`, `torch`, `edge_tts`, `kokoro` imports live only here. |
| `echo.py` | 288 | Self-echo guard: subtracts recent output band-energies from mic input so the assistant never answers its own voice tail; supports voice barge-in (currently off). Used by `main.py` `_listen_audio`. | Keep. |
| `viseme.py` | 275 | Text→viseme table (`VISEMES`, 13 shapes) + `VisemeStream` fusing transcript shapes with audio timing. Latin/Cyrillic/Greek via Unicode reduction; **CJK/Arabic/Devanagari/Hebrew/Thai fall back to audio-only mouth** (coverage < 0.55). | Lip-sync core. **Urdu/Arabic lip-sync degrades to audio-only — i18n gap for R6.** |
| `avatar.py` | 715 | Software-rendered holographic head (QPainter, numpy posing). Consumes PCM level + viseme frames from main.py; brows/gaze/blink/stress-nod; theme-agnostic colors. | Avatar component for R4. |
| `avatar_mesh.py` | 351 | Builds head mesh around MediaPipe canonical face model; loads `core/face_model.obj` (468 verts / 898 tris, Apache-2.0, ~25 KB). | Avatar asset — keep, rebrand-safe (no branding in geometry). |
| `audio_devices.py` | 421 | Mic/speaker picker by **device name** (not index); filters `sd.query_devices()` duplicates across host APIs; caches on background thread. Used by main.py + ui.py `AudioDeviceOverlay`. | Keep. |
| `confirm.py` | 161 | **The permission gate that matters:** `request(key, title, detail, run)` parks irreversible actions behind an on-screen CONFIRM/CANCEL banner (`TIMEOUT_SECONDS = 90`). Token issued by UI, never by the model. `bind()` called once from main.py:2053. | **Tool+permission engine seed (R3). Currently only `computer_settings` irreversible actions use it.** |
| `undo.py` | 121 | Shared undo stack (`push_undo`); `undo` tool lists/reverts. Only main.py references `undo_stack` — adoption by actions is thin. | Extend for R3. |
| `installer.py` | 138 | MARK XL auto-installer (`_CORE`, `_WINDOWS`, `_STT`, `_TTS` package lists). | **DEAD CODE — zero importers.** setup.py is the real installer. |

### 3.2 `actions/` (20 files, 10,258 lines) — 16 live tools + 4 helpers

Tool dispatch: `JarvisLive._execute_tool` (main.py:1113) handles 8 inline tools, then `self._action_registry` (16 tools), then `self._plugin_registry`.

| File | Lines | Tool name | Capability | Risk notes |
|---|---|---|---|---|
| `browser_control.py` | 1132 | `browser_control` | Playwright automation: navigate, click, type, JS eval, scrape. 18 jarvis mentions. | Powerful; platform-guarded bits (53 win32 checks repo-wide). |
| `game_updater.py` | 1101 | `game_updater` | Steam game updates (PUBG/CS2/GTA5 app IDs); uses `config.get_os()`. | Niche; subprocess use. |
| `computer_settings.py` | 962 | `computer_settings` | Volume/brightness/WiFi/shutdown/restart; **the ONLY action using `confirm.request`** for `_IRREVERSIBLE` (line 826). | Confirm-gate pattern to generalize in R3. |
| `file_processor.py` | 923 | `file_processor` | PDF/DOCX/XLSX/PPTX/CSV/audio read+analyze (pdfplumber, PyPDF2, python-docx, pandas/pydub optional). | Optional heavy deps are lazy + messaged. |
| `file_controller.py` | 769 | `file_controller` | Move/copy/rename/delete files (send2trash). | Destructive ops; no confirm gate today — R3 gap. |
| `dev_agent.py` | 639 | `dev_agent` | Plans + writes multi-file projects, `pip install`s deps, **runs an LLM-generated `run_command` via `subprocess`** (`_run_project`, line 298), opens VS Code. | **Arbitrary shell via LLM today.** R3 must gate. |
| `code_helper.py` | 633 | `code_helper` | Code Q&A/explain via `call_llm_text`. | Check whether it executes code — see §10 punch list. |
| `computer_control.py` | 589 | `computer_control` | Raw mouse/keyboard: type, click, hotkey, scroll, clipboard, screenshot, `focus_window` (Win32 powershell). | **Full desktop control with no gate** — Win+R → arbitrary command is one hotkey+type away. R3 must gate. |
| `desktop.py` | 518 | `desktop_control` | Window/app management. | |
| `youtube_video.py` | 475 | `youtube_video` | Transcript fetch + summarize. | |
| `web_search.py` | 416 | `web_search` | `ddgs` → fallback `duckduckgo_search`; news mode. | |
| `flight_finder.py` | 405 | `flight_finder` | Flight page parse via `core/gemini.py`. | |
| `reminder.py` | 367 | `reminder` | Reminders (win10toast on Windows; systemd/`at` elsewhere). | Windows-only toast path UNVERIFIED. |
| `send_message.py` | 296 | `send_message` | WhatsApp/Telegram via **pyautogui GUI automation** (paste + hotkeys), not APIs. | Brittle by design; no stored WA creds. |
| `open_app.py` | 293 | `open_app` | Launch apps by name. | |
| `screen_processor.py` | 183 | *(helper, no TOOL)* | `_capture_screen`, `_capture_camera` — used by `screen_process` inline tool. | Vision path. |
| `system_monitor.py` | 200 | *(helper, no TOOL)* | `get_system_status`, `SystemMonitor`; NVML GPU via ctypes (no subprocess). | Used by `system_status` inline tool. |
| `background_monitor.py` | 159 | *(helper, no TOOL)* | Topic monitors (`manage_monitor` tool), background watcher threads. | |
| `proactive.py` | 127 | *(helper, no TOOL)* | `ProactiveEngine` — proactive briefs/check-ins. | |
| `weather_report.py` | 71 | `weather_report` | Weather fetch. | |

### 3.3 `dashboard/` — FastAPI phone remote

| File | Role |
|---|---|
| `server.py` (~886 lines) | `DashboardServer`: plain HTTP on **port 8000** (+8001 alias when self-signed HTTPS enabled). AES-256-CBC app-layer crypto (`_AES_SALT = b'JARVIS-DASHBOARD-v1'`, SHA-256(session_key‖salt) key derivation — **fixed salt**, server.py:73-78). Auth: 6-char one-time PIN (`new_key`, 600 s expiry) shown as QR on desktop → `/auto-login` issues `secrets.token_urlsafe(32)` bearer token stored in `sessionStorage` as `jarvis_token` (+ `jarvis_key` PIN, + persistent `jarvis_device_token` in localStorage for auto re-login). Endpoints: `/login`, `/api/command`, `/api/wake`, `/api/upload` (500 MB cap, sanitized filenames → `~/Downloads/JARVIS Uploads`), `/api/files`, `/uploads/{file}`, `/ws` (feed + commands), `/ws/phone-audio` (PCM16 mic relay → server's Live session). Firewall auto-open via UAC-elevated `.bat` on Windows (`_ensure_network_access`, UNVERIFIED). Self-signed cert generated on first run → `config/certs/` (gitignored). |
| `static/login.html` | PIN entry page. |
| `static/app.html` | Phone UI: command feed, **tap-to-talk mic button** (`mic-btn`, "Voice — tap to speak") streaming PCM16 over websocket with resample to 16 kHz, file upload, toasts. CryptoJS served locally (auto-downloaded once). Has a chrome://flags "insecure origins" workaround card for HTTP mic access. |
| `static/crypto-js.min.js` | Vendored CryptoJS for AES in browser. |
| `__init__.py` | Empty. |

**Security posture (verified statically):** the Gemini API key **never goes to the browser** — `_get_gemini_key()` (server.py:61) is defined but **dead code** (zero callers repo-wide); phone audio is relayed server-side into the desktop's Live session. Key exposure risk is therefore low *by design*; the weak points are the 6-char PIN (~25 bits entropy, no rate limiting observed), the fixed AES salt, and plain-HTTP default. See §10.

### 3.4 `memory/`

| File | Lines | Role |
|---|---|---|
| `memory_manager.py` | 478 | JSON store `memory/long_term.json` (gitignored): `load_memory`, `save_memory`, `update_memory`, `search_memory` (scored keyword search), `remember`/`forget`, `format_memory_for_prompt`, session summaries (`save_session_summary`/`pop_last_session`), trim notifier. Thread-locked. |
| `config_manager.py` | 383 | **All runtime config in `config/api_keys.json` (PLAINTEXT JSON, gitignored):** `gemini_api_key`, `assistant_name` (default "JARVIS"), `user_name`, `voice_name` (Charon/Puck/Kore/Fenrir/Aoede), `wake_word_enabled`, `push_to_talk_enabled`, `hud_style` (face/core), thinking/turn-tuning/proactive-audio flags, media resolution, `input_device`/`output_device` (names), `plugins_enabled`, `plugin_config{namespace}` (per-plugin tokens/settings), `llm_provider`/`llm_url`/`llm_model`, `morning_brief_enabled`. No encryption anywhere. |
| `__init__.py` | 0 | Empty. |

### 3.5 `plugins/`

| File | Role |
|---|---|
| `_template.py` (45 lines) | Drop-in template: `PLUGIN = {name, description, parameters}` + `run(parameters, player, session_memory)`. Files starting with `_` are skipped by discovery. |
| `__init__.py` | Empty. |

**No production plugins ship** — only the template. (requirements.txt lists Gmail/Calendar OAuth + Tuya + MQTT extras "for plugins" that have no corresponding plugin files.)

### 3.6 `config/`

| File | Role |
|---|---|
| `__init__.py` (26 lines) | `get_os()`/`is_windows()`/`is_mac()`/`is_linux()` — reads `os_system` from `api_keys.json`, else auto-detects. Used by actions to branch OS behavior. |
| `jarvis.ico` | Windows app icon (branding asset — needs replacing in rebrand). |
| *(runtime, gitignored)* | `api_keys.json` (plaintext secrets), `certs/` (TLS key+cert). |

---

## 4. Dependency map

**Hard runtime (imported at module top in live code):** `PyQt6`, `sounddevice`, `numpy`, `google-genai` (as `google.genai`), `requests`, `psutil`, `PIL`, `pyautogui`, `pyperclip`.

**Lazy/guarded (imported in try/except or inside functions — one feature degrades, app survives):** `playwright`, `cv2`, `mss`, `pygetwindow`, `ddgs`→`duckduckgo_search` fallback, `pdfplumber`, `PyPDF2`, `docx`, `pptx`, `openpyxl` (via pandas), `pynvml` (system_monitor, ctypes-based NVML — pynvml package itself listed), `fastapi`/`uvicorn`/`cryptography`/`python-multipart` (dashboard fully optional), `qrcode` (QR overlay), `comtypes`/`pycaw`/`win10toast`/`pywinauto`/`pywin32`/`wmi` (Windows-only), `pandas`/`pydub` (optional, user-messaged), `openwakeword` (opt-in one-click install).

**In requirements but with NO shipped importer (install bloat — Phase 2):** `tinytuya`, `paho-mqtt`, `google-api-python-client`, `google-auth-oauthlib`. (Plugin extras for plugins that don't exist in the repo.)

**Imported but NOT in requirements:** only inside dead code (`miniaudio`, `torch`, `edge_tts`, `kokoro`, `faster_whisper`, `vosk` — all in `core/stt.py`/`core/tts.py`, zero importers). No live-code gap found statically.

**OS-specific:** 53 platform guards across 14 files (`sys.platform == "win32"` / `platform.system()` / `config.is_windows()`). Windows-only pip set: comtypes, pycaw, win10toast, pywinauto, pywin32, wmi. Linux/macOS shell out to native tools (`pactl`, `brightnessctl`, `osascript`, `xdg-open`) — no extra packages.

**Python:** 3.11–3.13 (setup.py gates; warns above 3.13).

---

## 5. Config / auth / credential map

| Secret / credential | Where it lives | Protection | Notes |
|---|---|---|---|
| Gemini API key | `config/api_keys.json` → `gemini_api_key` | **NONE — plaintext JSON** (`config_manager.save_api_keys` writes raw). Gitignored, but readable by any process/user on the machine. | Read by `core/gemini.py:api_key()` (cached), `memory/config_manager.get_gemini_key()`. Entered via `SetupOverlay` UI. |
| Dashboard session PIN | In-memory `self._pending_keys` (6 chars, A–Z0–9 minus confusables, 600 s TTL) | One-time use, expires | Displayed on desktop as QR (`RemoteKeyOverlay`, ui.py:2691). |
| Dashboard bearer tokens | In-memory `self._tokens` set; browser `sessionStorage.jarvis_token` | `secrets.token_urlsafe(32)` | Lost on restart (by design). |
| Dashboard persistent device token | `localStorage.jarvis_device_token` on phone + `self._device_sessions` server-side | Revocable via `/api/revoke-devices` | Auto re-login for known devices. |
| Dashboard message crypto | AES-256-CBC, key = SHA-256(PIN ‖ `b'JARVIS-DASHBOARD-v1'`) | **Fixed salt** (server.py:73); no PBKDF2 (deliberate, documented) | Phone encrypts `enc` payloads; server decrypts. Plaintext `text` also accepted on `/api/command` and `/ws`. |
| TLS | `config/certs/jarvis.key` + `jarvis.crt`, self-signed, generated on first run, chmod 600 (no-op on Windows) | Gitignored | HTTPS optional; default is plain HTTP on LAN. |
| Per-plugin tokens | `config/api_keys.json` → `plugin_config{namespace}` | **Plaintext** | Generic store; no plugin ships today. |
| OAuth tokens (Gmail/Calendar) | `**/token*.json`, `**/client_secret*.json` (gitignored patterns) | N/A | No such plugin ships; patterns are aspirational. |
| WhatsApp session | `config/whatsapp_web/` (gitignored) | N/A | Referenced in .gitignore; no WA-web code ships (send_message uses GUI automation). |

---

## 6. AI provider map

| Provider / path | Entry | Key handling | Models | Status |
|---|---|---|---|---|
| **Gemini Live (primary)** | `main.py` → `genai.Client(api_key=...)`, `aio.live.connect` | Plaintext `config/api_keys.json` | `LIVE_MODEL = "models/gemini-3.1-flash-live-preview"` (main.py:99); fallback in gemini.py: `models/gemini-3.1-flash-live-preview` | Live. Tools = 8 inline + 16 actions + N plugins as `function_declarations`. |
| **Gemini one-shot ladder** | `core/gemini.py` `call()/text()/as_json()` | Same key, cached, `threading.Lock` | FAST/SMART: `LIVE → gemini-2.5-flash-lite → gemini-2.5-flash`; SEARCH: REST-only `gemini-2.5-flash → gemini-flash-latest → gemini-2.5-flash-lite`; 10 s min timeout; 429 → 5-min cooldown | **2.5-era pinned names — verify currency in Phase 2** (repo predates any 3.x text-model rename). |
| **Ollama / OpenAI-compatible** | `core/llm_client.py` | None (localhost) | `llm_model` default `llama3.2`, `llm_url` default `http://localhost:11434` | Used by code_helper/dev_agent. Auto-launches `ollama serve` (Windows: `CREATE_NO_WINDOW`). |
| **Whisper/Vosk offline STT** | `core/stt.py` | N/A | faster-whisper `base` | **Dead code.** |
| **EdgeTTS/Kokoro/ElevenLabs** | `core/tts.py` | ElevenLabs key via config dict (dead path) | — | **Dead code.** |

**Key never reaches the browser** (verified: `dashboard/server.py::_get_gemini_key` has zero callers; phone mic is relayed as PCM into the server-side Live session).

---

## 7. Audio pipeline map

**Input (mic):** `sounddevice` InputStream → `main.py::_listen_audio` callback → wake-word gate (if asleep, frames go to `WakeWordDetector.feed()`, **never streamed**) → echo-tail subtraction (`core/echo.py`) → barge-in level check → `out_queue` → `_send_realtime` → `session.send_realtime_input(audio=Blob(pcm))`. Device = name-resolved via `core/audio_devices.py` (config `input_device`, "" = system default).

**Phone input:** `/ws/phone-audio` → `_phone_audio_queue` → `_phone_audio_relay()` (main.py:1988) → same Live session. Phone tap-to-talk button in `app.html` (PCM16 @16 kHz over websocket).

**Output:** Live session audio chunks → `_pcm_level()` (RMS) + `_pcm_visemes()` (formant analysis, main.py:112/158) → `sounddevice` playback → `VisemeStream.frames()` fuses audio timing with transcript shapes → `ui.push_visemes()` → `HudCanvas`/avatar mouth. Barge-in: mic levels while speaking can cut the reply.

**Push-to-talk:** `core/hotkey.py` (Ctrl+Space; global on Windows via `GetAsyncKeyState`, window-scoped elsewhere) → `JarvisLive._on_ptt` (main.py:900) → mic gate.

**Wake word:** `core/wake_word.py` (`hey_jarvis` ONNX, opt-in) → `_on_wake_detected` → `wake()` (main.py:679); auto-sleep after 2 min silence (`sleep()`, main.py:688). While asleep: typed commands and dashboard commands are gated (dashboard commands **do** wake it — deliberate, main.py:2025).

---

## 8. UI map (`ui.py`, 5,437 lines, 250 methods)

| Class (line) | Role |
|---|---|
| `C` (73) | Theme color constants. |
| `_SysMetrics` (228) | CPU/RAM/GPU/background sampler thread for HUD telemetry. |
| `HudCanvas` (382) | Central canvas: avatar head (`core/avatar.py`, default) **or** reactor core fallback (config `hud_style`); waveform, status text, camera stream. `face_path` param is legacy — mesh comes from `core/face_model.obj`; "face.png" branch was already removed (ui.py:864 comment). |
| `LogWidget` (981) | Terminal-style chat log. |
| `SetupOverlay` (1370) | First-run: API key entry → `config/api_keys.json`. |
| `CustomizeOverlay` (1593), `HueWheel` (1499) | Live theming. |
| `PluginManagerOverlay` (1811), `PluginSettingsOverlay` (2435) | Plugin enable/disable + per-plugin settings forms (from `PLUGIN_SETTINGS` schemas). |
| `ConfirmBanner` (1944) | The permission UI for `core/confirm.py`. |
| `AudioDeviceOverlay` (2024) | Mic/speaker picker. |
| `MemoryOverlay` (2153) | View/delete stored memory. |
| `RemoteKeyOverlay` (2691) | QR pairing (qrcode, lazy import) + manual key + expiry countdown. |
| `ClipboardPanel` (2349), `FileDropZone` (1111), `_CameraPreview` (1303) | Content panels. |
| `MainWindow` (2919) | Shell: HUD layout, ⚙ settings drawer, quick drawer, status bar, overlays, tray. |
| `JarvisUI` (5216) | Facade: `set_state`, `write_log`, `push_visemes`, `speak` hooks, `show_content/quiz/review`, `wait_for_api_key`, callbacks (`on_text_command`, `on_remote_clicked`, `on_wake_toggle`, …). |

---

## 9. Branding touchpoint inventory (Phase-3 sizing)

Case-insensitive "jarvis": **261 occurrences across 32 files.**

| File | Count | Notes (user-facing?) |
|---|---|---|
| `main.py` | 70 | `[JARVIS]` log prefixes, `JarvisLive`, `JarvisUI`, "Hey Jarvis" strings, `shutdown_jarvis` tool name |
| `ui.py` | 53 | `JarvisUI`, `J.A.R.V.I.S` default avatar name (line 383), log strings, tooltips |
| `dashboard/server.py` | 24 | `jarvis_token`/`jarvis_key`/`jarvis_device_token` (sessionStorage keys — **must stay in sync** with app.html), `JARVIS-DASHBOARD-v1` salt, `JARVIS Uploads` dir, firewall rule names, cert CN "JARVIS Dashboard" |
| `actions/browser_control.py` | 18 | Log strings |
| `readme.md` | 16 | Title/body "MARK LIV (54)", "JARVIS" prose |
| `actions/game_updater.py` | 11 | |
| `dashboard/static/login.html` | 9 | User-visible login page |
| `dashboard/static/app.html` | 8 | User-visible phone UI + token key names |
| `core/wake_word.py` | 8 | `hey_jarvis` model id, log strings — **functional, not just cosmetic** |
| `plugins/_template.py` | 5 | Template docs |
| `memory/config_manager.py` | 5 | Default `assistant_name = "JARVIS"` (functional default) |
| `core/avatar.py` | 5 | Comments |
| `core/audio_devices.py` | 3 | |
| `actions/reminder.py` | 3 | |
| `setup.py` | 2 | "MARK LIV setup" print |
| 14 more files | 1–2 each | `computer_control`, `computer_settings`, `desktop`, `file_processor`, `flight_finder`, `screen_processor`, `weather_report`, `dev_agent`, `code_helper`, `youtube_video`, `proactive`, `background_monitor`, `prompt.txt` (1), `plugin_loader`, `llm_client`, `memory_manager`, `requirements.txt` (1: "MARK LIV") |

**Other brand marks:** "Mark LIV"/"MARK LIV"/"Mark-LIV" — readme.md (8), setup.py (3), ui.py (1), requirements.txt (1), memory/config_manager.py (1). "MARK XL" — core/llm_client.py, core/stt.py, core/tts.py, core/installer.py (1 each, all legacy/dead-code headers). "MARK LIII" — .gitignore header (1). "Tony Stark" — main.py (1). `config/jarvis.ico` — binary icon asset. `face_model.obj` — no branding (geometry only).

**Rebrand-critical functional touchpoints (not just strings):** `hey_jarvis` wake model id; default `assistant_name="JARVIS"` in config_manager; `jarvis_token`/`jarvis_key` storage keys shared between server.py ↔ app.html; `_AES_SALT` value; `shutdown_jarvis` tool name (in Live tool declarations); `J.A.R.V.I.S` avatar default name.

---

## 10. Phase-2 stabilization punch list (do not fix in Phase 1)

1. **Plaintext secrets.** `config/api_keys.json` holds the Gemini key + future plugin tokens with zero encryption (config_manager.py, gemini.py). → Migrate to OS keyring / encrypted store (R-workstream: SaaS/auth).
2. **Dead code.** `core/stt.py`, `core/tts.py`, `core/installer.py` have **zero importers** (MARK XL leftovers). Decide: delete/quarantine or wire up. Their imports (`faster_whisper`, `vosk`, `edge_tts`, `kokoro`, `torch`, `miniaudio`) are the only requirements.txt gaps and all live here.
3. **Requirements bloat.** `tinytuya`, `paho-mqtt`, `google-api-python-client`, `google-auth-oauthlib` are installed but imported nowhere (no such plugins ship).
4. **Dashboard hardening.** 6-char PIN (~25 bits, no rate limit observed), fixed `_AES_SALT`, plaintext-`text` fallback accepted on `/api/command` and `/ws`, plain-HTTP default on LAN, UAC firewall flow UNVERIFIED on Windows.
5. **LLM can execute arbitrary shell TODAY.** `dev_agent` runs LLM-generated `run_command` via subprocess (actions/dev_agent.py:298); `computer_control` gives ungated mouse+keyboard (Win+R → type → Enter); `file_controller` deletes without confirm. The `core/confirm.py` gate exists but only `computer_settings` uses it. → Generalize the permission engine (R3) before any SaaS/multi-user exposure.
6. **Pinned model names aging.** `core/gemini.py` ladders pin `gemini-2.5-flash(-lite)`; `LIVE_MODEL = "models/gemini-3.1-flash-live-preview"`. Verify against current Google model catalog at Phase-2 time.
7. **`face.png` legacy param.** `main()` → `JarvisUI("face.png")` → `MainWindow` → `HudCanvas`; the mesh actually loads from `core/face_model.obj` (avatar_mesh.py:34). Harmless but confusing; clean during UI rework.
8. **`_get_gemini_key()` dead in dashboard/server.py:61.** Either remove or document as intentional (key stays server-side).
9. **Windows runtime UNVERIFIED.** 53 platform guards in 14 files; `GetAsyncKeyState` hotkey, pycaw/comtypes audio, win10toast, pywinauto, WMI, UAC firewall script, `CREATE_NO_WINDOW` — none exercised on Linux. Phase 2 must run the real boot path on Windows.
10. **`code_helper.py` execution check.** It uses `call_llm_text`; confirm it never `exec()`s generated code (grep showed no `exec(`/`eval(` hits, but verify the run path).
11. **Viseme i18n gap.** Arabic-script (Urdu/Arabic) text → audio-only mouth (viseme.py coverage rule). If R6 wants real Urdu lip-sync, this needs a pronunciation layer.
12. **Wake-word rebrand.** `hey_jarvis` ONNX model is the only pretrained phrase; "Hey Shirazi" needs a new model or phrase strategy (R-workstream: wake word).

---

## 11. Cross-reference: components → mission workstreams

*(Labels R1–R9 = mission workstreams from the task brief. Coordinator: pin to exact requirement numbers.)*

| Workstream | Existing foundation (file:line) | Gap for the mission |
|---|---|---|
| **R1 Full rebrand** | Defaults: `memory/config_manager.py` (`assistant_name`→"JARVIS"); `core/prompt.txt` `{assistant_name}`; ui.py:383 `J.A.R.V.I.S`; §9 inventory (261 hits/32 files + icon + salt + storage keys) | String sweep + functional renames (wake model, tool names, storage keys, salt, icon, readme/setup). |
| **R2 AI provider abstraction** | `core/gemini.py` (single Gemini choke point), `core/llm_client.py` (Ollama/OpenAI-compatible) | Unify into one provider interface; add key rotation; add new providers (Groq/OpenRouter per mission "free-first"). |
| **R3 Tool + permission engine** | `core/action_loader.py` + `core/plugin_loader.py` (discovery/validation), `core/confirm.py` (UI-issued gate), `core/undo.py`, `main.py:1113` `_execute_tool` | Generalize confirm gate to risk tiers; gate dev_agent/computer_control/file_controller; permission manifests per tool; audit log. |
| **R4 PyQt6 UI + avatar/reactor/lip-sync** | `ui.py` (all), `core/avatar.py`, `core/avatar_mesh.py`, `core/viseme.py`, `main.py:112` `_pcm_level`, `main.py:158` `_pcm_visemes` | Keep renderer; retheme/rebrand; fix Urdu visemes (§10.11); hotkey UNVERIFIED on Win. |
| **R5 FastAPI mobile dashboard + tap-to-talk** | `dashboard/server.py`, `static/app.html` (mic-btn PCM16 WS), `static/login.html`, `main.py:1988` phone-audio relay, `:2069` server boot | Already Siri-like tap-to-talk; needs R3 permissions, §10.4 hardening, i18n of phone UI. |
| **R6 i18n en/ur/ar + Roman Urdu** | Prompt templating (`prompt.txt` + `_build_config`); Live is multilingual by nature; viseme Latin/Cyrillic/Greek | No locale framework; Arabic-script lip-sync gap; wake phrase; RTL in Qt + phone UI; Urdu TTS voice selection. |
| **R7 Research mode** | `web_search` (ddgs), `flight_finder`, `youtube_video` (transcripts), `screen_process` vision tool, `core/gemini.py:SEARCH` grounded ladder | New dedicated research pipeline; the SEARCH tier is the seed. |
| **R8 Layered memory** | `memory/memory_manager.py` (JSON LTM + session summaries + `recall_memory` tool + MemoryOverlay UI) | Layering (working/episodic/semantic), embeddings/vector search, encryption at rest. |
| **R9 SaaS foundation** | Dashboard auth (PIN→bearer), `.gitignore` secret hygiene, `setup.py` installer | Multi-user auth, encrypted config (§10.1), sandboxing of tool execution (§10.5), audit logging, update channel. |

---

## 12. Audit honesty log

- **Verified statically:** file inventory, LOC counts, git state, `py_compile` on all 48 modules, import graph vs requirements.txt, TOOL/PLUGIN discovery shapes, dashboard auth flow, credential storage format (read code, not executed), dead-code detection via repo-wide importer grep, branding counts via case-insensitive grep.
- **UNVERIFIED (needs Windows runtime in Phase 2):** every `win32`-guarded path (hotkey polling, pycaw volume, win10toast, pywinauto, WMI, UAC firewall `.bat`, `CREATE_NO_WINDOW`), Playwright browser automation, openwakeword model download/inference, actual Gemini Live session behavior, dashboard phone pairing over LAN, QR rendering.
- **Not audited:** `face_model.obj` binary contents (only its loader + vertex counts from comments); `jarvis.ico` (binary); `crypto-js.min.js` (vendored third-party, assumed intact).
- No code was modified; no commits made. Upstream repo untouched.
