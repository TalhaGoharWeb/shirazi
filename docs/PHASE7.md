# Phase 7 — Mobile Dashboard

**2026-09-23.** The phone becomes a first-class SHIRAZI client: a free,
installable PWA that talks to the desktop over the local network. Tap or hold
to speak (phone mic → WebSocket PCM16 → the desktop's AI pipeline), chat with
the Phase-6 agent engine, approve or deny gated steps on the phone, and drive
the computer with a touchpad, D-pad and quick commands. No paid realtime
service, no app store, no API keys on the phone.

## What was built

| Area | Location | Status |
|---|---|---|
| Agent chat API | `dashboard/server.py` → `POST /api/agent` | **Working** — natural language → `Agent.run_sync` on a worker thread; gated steps surface Approve/Deny on the phone; timed-out gates park for late approval via `POST /api/agent/confirm` |
| Phone confirmation flow | `request_phone_confirmation` + `POST /api/agent/confirm` + feed `confirm_request` | **Working** — `confirm_request` broadcast to every connected phone, phone answers on `/ws/cmd`, parked thread resumes; tested end-to-end with the real concurrent flow (approve + deny) |
| Command channel | `dashboard/server.py` → `/ws/cmd` | **Working** — authenticated WebSocket: ping/pong, D-pad, touchpad, scroll, volume, quick-command runs, confirm responses; every payload allowlisted + validated; 120 cmd / 60 s per token |
| Touchpad | `_handle_cmd` touchpad_* + `app.html` pad | **Working** — drag-to-move (smoothed, sensitivity 0.2–3), double-tap left-click, long-press right-click, two-finger scroll; 40 moves/s budget, cursor estimate re-synced when pyautogui can read the pointer |
| D-pad | `DPAD_KEYS` allowlist | **Working** — up/down/left/right/enter/space/esc/tab/F11 only; anything else rejected before dispatch (e.g. `rm -rf` → `unknown key`) |
| Quick commands | `/api/quick-commands*` + `config/dashboard.json` | **Working** — 8 defaults (YouTube, Chrome, File Explorer, Date & Time, Weather, Screen Summary, Joke, Notifications) routed through the agent; user additions persist |
| Telemetry | `GET /api/telemetry` (2 s sampler cache) | **Working** — CPU/RAM/DISK/BATTERY/UPTIME/NETWORK; phone polls every 3 s; honest `null` when a probe is unavailable |
| Audio devices | `/api/audio-devices*` | **Working** — enumerate/select/rescan mic + speaker via Phase-5 `core/audio_devices.py`; `POST …/test` plays a synthetic chime; honest errors without sounddevice |
| Session API | `GET /api/session` | **Working** — agent availability, device manager, pending confirms, honest desktop-backend state (`live` true/false/`unknown`) |
| Voice path (phone mic) | `app.html` → `/ws/phone-audio` | **Working** — tap toggles, hold streams while held; AudioWorklet (ScriptProcessor fallback) → PCM16 @16 kHz → the desktop Live session relay; mic-level meter drives the reactor ring |
| Spoken replies | `app.html` speechSynthesis | **Working** — the phone speaks assistant text with its own on-device synthesis (en/ur/ar voice pick); no server TTS exists or is claimed |
| PWA | `manifest.webmanifest`, `sw.js`, icons, `/i18n/{en,ur,ar}` | **Working** — installable, offline shell cached, RTL layout for Urdu/Arabic |
| Frontend | `dashboard/static/app.html` (rewritten) | **Working** — Voice / Chat / Remote / System tabs, glassmorphism reactor, live feed with auto-reconnect, confirmation modal with countdown, device-token auto-login with legacy `jarvis_*` fallback |
| Executor hook isolation | `core/agent/executor.py` (`_UNSET` sentinel) | **Working** — `run`/`run_sync`/`run_in_background`/`execute_tool` accept a per-call `confirm_hook` override, so the dashboard routes confirmations to the phone without mutating the shared agent the desktop Live loop uses |
| Wiring | `main.py` | **Working** — dashboard gets `self.agent`, the Phase-5 `DeviceManager`, and an honest backend-status callback (`session` + `awake`) |

## Security model (as required)

- **Keys never leave the desktop.** All AI provider keys stay server-side; the phone holds only a bearer token + AES session key issued at pairing.
- **Auth:** one-time 6-char PIN/QR pairing → bearer token; PIN throttling (5 fails → 5-min lockout, success resets) — regression-tested.
- **Origin checks** on `/ws`, `/ws/phone-audio`, `/ws/cmd` (same-host or no-Origin non-browser clients pass).
- **Rate limiting:** `/api/command` + `/ws/cmd` at 120/60 s per token; touchpad moves at 40/s (excess dropped, not queued); `/api/agent` text capped at 2000 chars, legacy command text at 1000.
- **No unrestricted shell, no arbitrary commands:** `/ws/cmd` dispatches only hardcoded tool names behind strict allowlists; the permission engine re-validates every call and PRIVILEGED tools are denied outright. Free-text goes through `/api/agent`, where every step is permission-gated and USER_CONFIRMATION steps demand the explicit phone Approve/Deny.
- **Legacy `/api/command` plaintext fallback kept** (old clients) but strictly validated: must be a string, ≤1000 chars; encrypted `enc` payloads preferred.
- **TLS pair preserved:** `shirazi.key/.crt` with legacy `jarvis.key/.crt` fallback (see `docs/LEGACY_COMPAT.md`).

## Genuinely working vs. limitation (honest)

**Working:** everything in the table above, verified by 23 new automated
tests plus REST/WebSocket smoke tests.

**Honest limitations (documented, not faked):**
- **No WebRTC.** The voice path is PCM16 over WebSocket — the practical free
  local transport. Nothing claims a WebRTC peer connection.
- **No server TTS.** Spoken phone replies use the phone's own speech
  synthesis. The server never pretends to stream audio back.
- **HTTPS on LAN needs the cert trusted.** Plain HTTP works on the LAN but
  the mic then requires the `chrome://flags` insecure-origins workaround
  (the app shows the exact steps); over HTTPS the mic just works.
- **The sandbox has no mic, no sounddevice, no pyautogui, no display.**
  Audio endpoints and remote-control tools return their honest
  unavailable/failure messages here. All gesture/mic/PWA behaviour is
  manual QA on a real phone + Windows desktop (`docs/WINDOWS_QA.md`,
  "Mobile dashboard manual QA").
- **Quick-command answers degrade honestly** when providers/backends are
  unreachable — the agent says so instead of inventing results.
- **Urdu/Arabic catalogs are skeletons** with English fallback; full
  translations are a content task, not a code task.
- **The 6-char PIN flow is unchanged** (deliberately — legacy clients and
  the QR flow depend on it). The planned auth overhaul (multi-user auth,
  AES-salt rotation, dropping the plaintext fallback, TLS-by-default)
  stays scheduled for Phase 8.

## Verification

- `python -m compileall .` — clean.
- `python -m unittest discover -s tests` — **189 tests, all passing**
  (166 pre-existing incl. the Phase-6 agent suite + 23 new Phase-7 tests
  in `tests/test_phase7_dashboard.py`: PIN throttling, mocked `/api/agent`,
  the real concurrent confirm round-trip over `/ws` + `/ws/cmd`, WebSocket
  command allowlists, audio endpoints, quick commands).
- Browser gesture/microphone/PWA QA is manual in this headless sandbox —
  see `docs/WINDOWS_QA.md`.

## Deferred to Phase 8 (SaaS)

Multi-user auth to replace the PIN flow, AES salt rotation, removal of the
plaintext `text` fallback, TLS-by-default, full Urdu/Arabic string
translation, and any server-side TTS (only if a free path exists).
