# 🤖 SHIRAZI

**The free-first, independent personal AI assistant for desktop + mobile.**

Shirazi hears you, sees your screen, speaks with a lip-synced holographic avatar,
and controls your computer — from your desk or from your phone. No subscription,
no vendor lock-in, no single AI provider: it runs on free tiers (Gemini,
OpenRouter, local Ollama) and every provider is swappable.

> **Origin note.** Shirazi began as a rebrand and rebuild of the open-source
> [Mark-LIV](https://github.com/FatihMakes/Mark-LIV) project by FatihMakes
> (see `LICENSE` for the upstream copyright). It has since been substantially
> rewritten: provider abstraction, permission engine, multi-user accounts,
> mobile dashboard, and SaaS foundations are all new. Legacy Mark-LIV / JARVIS
> compatibility shims (old tokens, old storage paths, old task names) are kept
> deliberately so existing users don't lose anything — see
> `docs/LEGACY_COMPAT.md`.

---

## Table of contents

1. [What is Shirazi?](#what-is-shirazi)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Installation](#installation)
5. [Windows setup](#windows-setup)
6. [Python version](#python-version)
7. [AI providers: Gemini / OpenRouter / Ollama](#ai-providers-gemini--openrouter--ollama)
8. [Mobile dashboard](#mobile-dashboard)
9. [Security](#security)
10. [Configuration](#configuration)
11. [Troubleshooting](#troubleshooting)
12. [Development](#development)
13. [Contributing](#contributing)
14. [License](#license)
15. [Known limitations](#known-limitations)

---

## What is Shirazi?

Shirazi is a voice-first AI assistant that lives on your computer and extends to
your phone:

- **Talk to it** — push-to-talk, optional local wake word, or type.
- **It talks back** — streaming speech with a holographic avatar that lip-syncs
  to what it says.
- **It does things** — open apps, control volume/media, browse, manage files,
  run code (with your confirmation), research topics, set reminders.
- **From your phone** — a mobile dashboard (PWA) on your Wi-Fi: tap-to-talk,
  remote control, telemetry, file access.
- **Your brain, your choice** — Gemini, OpenRouter, or a local Ollama model.
  Free tiers are the defaults; nothing phones home without a key you provided.

The project rule is **free-first**: every feature must work without paying
anyone, and paid paths are disclosed, never assumed.

---

## Features

| Area | What you get |
|---|---|
| 🧑‍🎤 Holographic avatar | Software-rendered head (QPainter + numpy, no GPU), real lip-sync from formants + transcript, gaze/blink/brows as status signals |
| 🎙️ Voice | Push-to-talk (Ctrl+Space; global on Windows), local wake-word option, self-echo guard |
| 🧠 AI providers | Gemini, Gemini Live (realtime audio), OpenRouter, Ollama — chainable with free-first ordering |
| 🔐 Permission engine | Every tool call is gated: SAFE / READ_ONLY / USER_CONFIRMATION / PRIVILEGED. Destructive or code-execution actions always ask; the phone tap counts as approval for remote control |
| 📱 Mobile dashboard | FastAPI + WebSocket PWA: voice, D-pad/touchpad, volume, telemetry, files, quick commands — PIN/QR pairing, no account needed |
| 🧠 Layered memory | Short-term session, long-term user memory, per-tool notes — encrypted at rest |
| 🌍 i18n | English, اردو (Urdu), العربية, Roman Urdu |
| 🔍 Research mode | Multi-source research dossiers with claim tags |
| 🧩 Skills | Actions and plugins share one shape (`TOOL`/`PLUGIN` dict + `run()`), auto-discovered at launch |
| ☁️ SaaS foundations | Multi-user accounts, device registry, per-user provider chain, usage metering (local-first; see `docs/SAAS.md`) |

---

## Architecture

```
┌─────────────┐      ┌──────────────────────────────┐
│  PyQt6 HUD  │◄────►│           main.py            │
│ (avatar,    │      │  Live loop · agent engine    │
│  reactor)   │      │  permission gate · memory    │
└─────────────┘      └──────┬───────────────┬───────┘
                            │               │
              ┌─────────────▼──┐   ┌────────▼──────────┐
              │ core/providers │   │ core/agent        │
              │ gemini · live  │   │ intent → plan →   │
              │ openrouter ·   │   │ gated tools →     │
              │ ollama         │   │ answer            │
              └────────────────┘   └───────────────────┘
                            │
              ┌─────────────▼──────────────────────────┐
              │ dashboard/server.py (FastAPI + WS)     │
              │ PIN/QR pairing · /ws/cmd · phone audio │
              │ → relays to the same agent engine      │
              └────────────────────────────────────────┘
```

Key directories:

| Path | Role |
|---|---|
| `main.py`, `ui.py` | Desktop app: Live loop, HUD, reactor, avatar |
| `core/providers/` | AI provider abstraction (Gemini, Live, OpenRouter, Ollama) |
| `core/agent/` | Intent → plan → permission-gated tool execution |
| `core/permissions.py` | The single gate every tool call passes |
| `core/accounts/` | Multi-user accounts, sessions, device registry, secrets |
| `actions/` | Auto-discovered skills (one file per skill) |
| `plugins/` | Plugin-format skills |
| `dashboard/` | Mobile PWA (FastAPI + WebSocket + static app) |
| `memory/` | Layered memory + config manager |
| `i18n/` | Translations |
| `docs/` | Phase docs, API, security notes, migration, Windows QA |

---

## Installation

**One command** (installs dependencies for your OS only, then launches):

```bash
python setup.py        # Windows:  py setup.py
```

Then start the assistant:

```bash
python main.py         # Windows:  py main.py
```

Manual install, if you prefer:

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

`requirements.txt` uses `sys_platform` markers, so one file serves Windows,
macOS and Linux — pip simply skips the lines for other OSes.

---

## Windows setup

1. Install **Python 3.11–3.13** from
   [python.org](https://www.python.org/downloads/) and tick
   **"Add python.exe to PATH"**.
2. In a terminal: `py setup.py` (installs everything, creates the desktop
   shortcut, offers autostart + firewall rule).
3. Run `py main.py`.
4. Open **⚙ → WAKE WORD** to enable the local wake word (downloads a tiny
   model on first use), and **Remote Control** to pair your phone.

Notes:

- Push-to-talk (**Ctrl+Space**) is truly global on Windows.
- Volume/brightness control uses native Windows APIs (`pycaw`, `wmi`).
- If Windows Firewall asks about Python, allow it on **private** networks —
  that's what lets your phone reach the dashboard.
- Windows-only runtime paths (audio device APIs, PyQt rendering, hotkeys,
  firewall rules) are exercised on real hardware — see
  [Known limitations](#known-limitations) and `docs/WINDOWS_QA.md`.

---

## Python version

- **Minimum: Python 3.11** (hard floor — older interpreters can't parse the code).
- **Tested through: Python 3.13.**
- Newer than 3.13 prints a warning, not an error.

`setup.py` checks this before installing anything.

---

## AI providers: Gemini / OpenRouter / Ollama

All keys stay on your machine (`config/api_keys.json`, ignored by git).
The app only ever asks providers for what each feature needs.

| Provider | Setup | Notes |
|---|---|---|
| **Gemini** | Free key at [aistudio.google.com](https://aistudio.google.com) → paste in ⚙ → Providers | Default chat + Live realtime voice |
| **OpenRouter** | Free key at [openrouter.ai](https://openrouter.ai) → ⚙ → Providers | Many free models; pick per task |
| **Ollama** | Install [ollama.com](https://ollama.com), `ollama pull llama3.1` | Fully local, no key, no network |

You can set a **provider chain** (e.g. Ollama → Gemini → OpenRouter): if one
fails or hits quota, the next takes over. Free tiers and local models are the
defaults; paid models need explicit opt-in.

> **External services, labelled:** Gemini / OpenRouter require your own free
> API key and an internet connection. Ollama requires the Ollama app and a
> downloaded model. Nothing else in Shirazi needs any external service.

---

## Mobile dashboard

On your desktop: open **Remote Control** in the Shirazi UI — it shows a QR code
and a PIN. On your phone (same Wi-Fi):

1. Open the dashboard URL shown (e.g. `http://192.168.1.5:8000`).
2. Scan the QR or enter the PIN.
3. Install it as an app (browser menu → *Add to Home Screen*) — it's a PWA.

From the phone you get: tap-to-talk voice, D-pad + touchpad + volume controls,
live telemetry, file browser, and quick commands. Pairing mints a device token
(stored hashed, shown once); already-paired phones keep working after the
desktop restarts, and **Revoke devices** kills every session a device created.

API surface is documented in `docs/API.md`.

---

## Security

The full audit is `docs/SECURITY_AUDIT.md`. The short version:

- **No secrets in the repo.** `config/api_keys.json`, `*.db`, `token*.json`,
  `client_secret*.json`, `config/certs/`, WhatsApp sessions are all git-ignored
  (verified with `git check-ignore`).
- **Keys never reach the frontend.** The dashboard API exposes only
  `key_configured: true/false` — never values. No key material in
  `dashboard/static/`.
- **Permission gate.** Every tool call passes `core/permissions.py`:
  unknown tools fail closed, PRIVILEGED tools need developer mode *and*
  confirmation, and code-execution sub-actions (`code_helper` run/build)
  always ask.
- **No shell interpolation.** Subprocess calls use argv form; window titles
  are sanitised before touching PowerShell; the Windows app launcher uses
  `os.startfile`, never `cmd.exe`.
- **Auth.** PIN/QR pairing with rate-limited attempts, per-device bearer
  tokens, revocation cascades to every session the device minted.
- **Known trade-offs (documented, not hidden):** LAN dashboard defaults to
  HTTP (TLS is opt-in via the cert pair the app generates); the legacy
  plaintext `/api/command` path stays for old clients; the legacy AES salt is
  fixed for backward compatibility. See `docs/SECURITY_NOTES.md` and
  `docs/SECURITY_AUDIT.md`.

---

## Configuration

All settings live in `config/` (never committed):

| File | What |
|---|---|
| `config/api_keys.json` | Provider keys, assistant name, tiers |
| `config/settings.json` | UI/behaviour settings |
| `config/shirazi_memory.db` | Encrypted layered memory |
| `config/dashboard.json` | Phone quick commands |

Edit from the app (⚙ panels) or the files directly. Environment variables are
read as overrides where documented; see `docs/MIGRATION.md` for how legacy
Jarvis/Mark-LIV data is detected and migrated.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `python` not found (Windows) | Reinstall Python with **Add to PATH** ticked, or use `py` |
| Mic hears nothing | ⚙ → Audio → pick the *named* input device (not "System default"); unplugged headsets fall back automatically |
| Wake word never fires | It listens for **"Hey Jarvis"** until a Hey-Shirazi model ships (see below) — enable it in ⚙ → Wake Word |
| Phone can't reach dashboard | Same Wi-Fi? Firewall allowed Python on **private** networks? Try the HTTPS alias URL |
| Provider quota errors | The chain fails over automatically; check ⚙ → Providers → chain order |
| App won't start after update | Delete `config/` (settings reset, memory re-created) — or see `docs/MIGRATION.md` rollback |

---

## Development

```bash
# run the test suite (venv with fastapi + cryptography)
python -m unittest discover -s tests

# add a skill: one file in actions/ with a TOOL dict and run()
# add a provider: subclass in core/providers/ and register it
```

Conventions: minimal diffs, free-first, never fake (estimates stay estimates),
never log secrets, never expose keys to the frontend. Phase history lives in
`docs/PHASE4.md` … `docs/PHASE7.md`; the security audit is
`docs/SECURITY_AUDIT.md`.

---

## Contributing

Issues and PRs are welcome. Please:

- Keep the free-first rule: no hard dependency on a paid service.
- Add tests for behaviour changes (`tests/`).
- Don't commit anything under `config/`, `*.db`, or key files.
- Update `docs/` when you change documented behaviour.

---

## License

MIT — see `LICENSE`. Upstream Mark-LIV/JARVIS copyright by FatihMakes is
retained there.

---

## Known limitations

Honest list — nothing here is planned to be hidden, only fixed or documented:

- **Wake word:** the only trained local model is `hey_jarvis`, so the app
  listens for **"Hey Jarvis"** (and reports it honestly in the UI) until a
  Hey-Shirazi model is trained. The *target* phrases "Hey Shirazi"/"Shirazi"
  are configurable for when that model exists.
- **Windows-only runtime paths are UNVERIFIED in CI:** audio device APIs,
  PyQt rendering, global hotkeys, firewall rules, and the Windows
  app-launcher path are exercised on real hardware only
  (`docs/WINDOWS_QA.md`).
- **Mobile-on-LAN** is tested via mocked clients; real phone ↔ PC runs are
  manual.
- **No password/OIDC login.** Multi-user accounts exist, but sign-in is
  local PIN/QR pairing — there is no password or cloud identity.
- **LAN dashboard defaults to HTTP** (same-network convenience); enable the
  generated TLS pair for HTTPS.
- **Legacy compatibility trade-offs:** fixed AES salt, plaintext legacy
  command fallback, and the pre-trained wake model are kept so old clients
  and old data keep working (`docs/LEGACY_COMPAT.md`).
- **Metering gaps:** Gemini Live usage and provider token counts are not
  fully metered yet.
- **Boot secret migration is intentionally incomplete** — see
  `docs/MIGRATION.md`.
- **Heavy optional features** (Playwright browser control, tesseract OCR)
  degrade gracefully when not installed.
