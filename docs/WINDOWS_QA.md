# Windows QA — Phases 5–9 (MANUAL where noted)

Everything marked MANUAL must be checked by a human on a real Windows
machine. The Linux sandbox has no PyQt6, no WASAPI/MME/DirectSound and no
audio hardware, so none of the items below can be verified there — the
automated suite (`python -m unittest discover -s tests`, **300+ tests**) plus
the 7 dedicated integration tests
(`python -m unittest tests.test_phase9_integration`) cover only the headless
logic of all phases through Phase 9. Do not mark an item done until you have
seen it with your own eyes.

> **Explicitly UNVERIFIED (Windows-only runtime):** real audio device APIs
> (WASAPI/MME/DirectSound), PyQt6 rendering, global hotkeys, firewall rules,
> the Windows app-launcher path (`os.startfile`), mobile-on-LAN against a
> real phone, and live AI providers. These are exercised on real hardware
> only — never claimed from CI.

## 1. Rendering

- [ ] App starts, main window paints: dark glassmorphism, subtle cyan,
      emerald accents, restrained gold, Islamic geometric rosette in the
      corner brackets. NOT a gaming UI — no heavy glow/bloom.
- [ ] Centrepiece cycles REALISTIC → HOLOGRAM → REACTOR with the drawer
      button (◈ RENDER MODE). Each switch is instant, no freeze.
- [ ] REALISTIC: shaded 3D avatar head; HOLOGRAM: wireframe/unshaded head;
      REACTOR: layered reactor widget — depth glow, vignette, HUD brackets,
      perspective ellipses, two gyroscope rings **turning slowly**,
      segmented iris, cyan arcs, hexagons, 8-point rosette, 12 spokes,
      72-point waveform ring, orbiting particles, central sphere, lens flare,
      scan sweep, telemetry + state indicators.
- [ ] Reactor reacts to live audio: ring/spokes/sphere/particles move with
      the mic while LISTENING and with the voice while SPEAKING; each state
      (IDLE/LISTENING/THINKING/SPEAKING/EXECUTING/SUCCESS/ERROR/OFFLINE)
      visibly biases sweep speed, iris, glow and particle motion.
- [ ] Avatar styles cycle default → kofia → turban → kufi (🎭 button):
      kofia shows geometric embroidery, turban is a wrapped scholar style,
      kufi a subtle geometric band; beard shading restrained; nothing
      caricatured. Style persists across restart.
- [ ] State label under the HUD shows translated state names; switch the
      language and confirm the label changes.

## 2. Language / RTL

- [ ] Settings drawer → language picker: English / اردو / العربية /
      Roman Urdu. Choice is saved and survives restart.
- [ ] Pick اردو or العربية: layout direction flips to right-to-left,
      ConfirmBanner/audio panel/telemetry/state labels render in the new
      language immediately. Remaining legacy strings translate on restart.
- [ ] Roman Urdu renders left-to-right with Latin strings.

## 3. Audio devices (WASAPI / MME / DirectSound)

- [ ] 🎧 AUDIO DEVICES panel lists real input and output devices.
- [ ] Each selected device shows: HOST API (e.g. WASAPI, not "n/a"),
      CHANNELS, SAMPLE RATE, STATUS (● READY / UNAVAILABLE).
- [ ] Unplug a USB headset → RESCAN (background, UI never freezes) →
      device shows "(not connected)"; Apply does **not** crash.
- [ ] Change the input device → Apply → session reconnects **without losing
      the conversation** (keep_context=True path).
- [ ] TEST MIC: live VU bar at ~30 fps, level %, dB, peak-hold, red
      ⚠ CLIPPING warning when shouting. Stop the test / close the panel →
      the mic is released (verify: another app can open the mic right after).
- [ ] PLAY TEST CHIME: three-tone chime (587 Hz + 880 Hz + 146 Hz) plays on
      the selected output; UI stays responsive while it plays.
- [ ] MIC GAIN slider 50–200 %: the model hears the gain change (speak at
      200 % — recognition still works, no digital hash on loud input).
- [ ] MASTER VOLUME 0–100 %: reply loudness follows the slider; the avatar's
      mouth and the HUD waveform still track the (quieter) speech.

## 4. Lip-sync

- [ ] Speak a reply in English: mouth opens on vowels (F1), spreads/rounds
      on F2, roughly one shape per 20 ms — no flapping, no frozen mouth.
- [ ] Arabic ("السلام عليكم") and Urdu ("آپ کیسے ہیں") replies: consonant
      closures land on the right sounds. Honest limit: unwritten short
      vowels in Arabic/Urdu script come from the audio formants, not the
      text — text mainly improves consonant closures.

## 5. Telemetry

- [ ] ◈ SYSTEM TELEMETRY panel: CPU / RAM / DISK / BATTERY bars ease
      smoothly (no jumping), UPTIME reads like "3d 4h 12m", NETWORK shows a
      real rate or "n/a" — never a blank or fake value.
- [ ] On a laptop without a battery sensor, BATTERY says "no battery" (or
      equivalent), not "0%".

## 6. Confirm banner (permission flow)

- [ ] Trigger a tool that needs confirmation: banner shows the translated
      title, tool name, what it will do, and Confirm/Cancel in the current
      language.

## 7. Performance / stability

- [ ] Opening the audio panel, rescanning, and the chime never freeze the
      UI (they run on QThread workers).
- [ ] Leave the app running 30+ minutes with telemetry open: no leak, no
      slowdown, mic test start/stop 10× without a stuck stream.

## 8. Agent engine (Phase 6 — MANUAL)

The sandbox has no display, no Playwright, no tesseract and no pyautogui,
so these were verified headless-only (166 unit tests, all passing). A
human on real Windows must see the real paths:

- [ ] Voice: "Open Chrome and search for today's weather" → the model
      calls `app_open` then `browser_search`; no silent browser driving.
- [ ] Voice: "Find the PDF I downloaded yesterday" → `search_files` with
      extension .pdf under Downloads; results read back.
- [ ] Voice: "Turn the volume down" → confirm banner appears (volume_set
      is USER_CONFIRMATION); Confirm lowers the volume, Cancel does not.
- [ ] Voice: "Summarize what's currently on my screen" → `vision_describe`
      captures and summarises; OCR text appears when tesseract is
      installed, honest "unavailable" when it is not.
- [ ] Voice: "delete the file X" → confirm banner appears; the file is
      untouched until Confirm; Confirm moves it to the Recycle Bin.
- [ ] Voice: "run the command …" → the shell banner shows the EXACT
      command; nothing runs until Confirm.
- [ ] `pip install playwright && playwright install chromium` → browser
      tools report availability; `browser_navigate`/`browser_click` work
      against a real page.
- [ ] Long agent runs (multi-step plans) never freeze the HUD: the loop
      stays on worker threads; the UI remains responsive.
- [ ] Research: "research the history of the astrolabe" → a report with
      FACT / SOURCE / INFERENCE / UNCERTAINTY sections; every citation
      opens a real page that was actually retrieved.

## 9. Mobile dashboard (Phase 7 — MANUAL)

Setup: start SHIRAZI on the Windows desktop, open **Remote Control**, and
pair the phone by scanning the QR code (or typing the 6-char key). The
dashboard URL is `http(s)://<desktop-ip>:<port>/`.

- [ ] Pairing: QR scan logs the phone in without typing; a wrong key 5×
      locks the phone out for ~5 minutes ("Too many attempts"); a correct
      key after 4 wrong ones still works (counter resets on success).
- [ ] PWA: browser menu → "Add to Home Screen" (or the INSTALL APP
      button) installs SHIRAZI; the installed app opens full-screen with
      the reactor icon; airplane mode shows the cached shell with an
      honest OFFLINE pill — no fake telemetry.
- [ ] Voice tab — **tap**: single tap starts listening (orb glows green,
      "🎤 Listening…", waveform ring dances with the mic level); tap again
      stops. **Hold**: press-and-hold streams only while held; release
      stops. Speak a command — the desktop answers in the chat feed and,
      with the 🔊 toggle on, the phone speaks the reply.
- [ ] Voice tab — offline: with the desktop app stopped, the pill shows
      OFFLINE and voice/chat show "connection lost" instead of fake
      answers.
- [ ] Chat tab: typing "open youtube" runs the agent and shows the answer;
      a gated request (e.g. "delete the temp file") pops the **🔒
      confirmation modal** with tool name, reason, args and a countdown —
      APPROVE runs it, DENY cancels it, and the inline pending card in
      the feed does the same when the modal is missed.
- [ ] Remote tab — touchpad: drag moves the desktop cursor (sensitivity
      slider changes the speed); double-tap = left click; long-press
      (~0.5 s, vibrates) = right click; two-finger drag scrolls.
- [ ] Remote tab — D-pad: arrows/⏎ move through a desktop menu or text
      field; SPACE/ESC/TAB/F11 work. Nothing outside the allowlist can be
      sent (there is no free-text key field by design).
- [ ] Remote tab — quick commands: the 8 defaults each produce a real
      agent answer (or an honest "unavailable" when the desktop has no
      provider keys); ＋ Add creates a custom command that survives an
      app restart.
- [ ] System tab: telemetry bars move (CPU/RAM/DISK/BATTERY/UPTIME/
      NETWORK), updating every ~3 s; audio selects list the real Windows
      mic/speakers; RESCAN picks up a newly plugged headset; TEST plays
      the chime through the chosen speaker.
- [ ] System tab — session: shows agent available ✓, backend live state
      honest (LIVE / not-live / unknown); REVOKE forces the phone to
      re-pair; LOG OUT returns to the login page.
- [ ] Language: switching to اردو flips the whole UI to RTL Urdu; broken
      strings fall back to English rather than showing blanks.
- [ ] HTTPS: with the desktop's `shirazi.crt` trusted on the phone, the
      mic works with no flags; over plain HTTP, the app shows the
      `chrome://flags` insecure-origins steps and the mic works after
      them. (A legacy `jarvis.crt`-paired phone keeps working — the
      server reuses the old cert.)

## 10. SaaS foundation (Phase 8 — MANUAL)

The automated suite (253 tests) covers the headless logic below; these
items need a human with two devices on the real LAN.

- [ ] Desktop boots with no `config/shirazi_accounts.db`: the `local`
      admin is auto-created silently and every old flow (voice loop,
      PIN pairing, `/api/command`) works exactly as before.
- [ ] `GET /api/v1/health` (no token) returns `ok: true`; every other
      `/api/v1/*` without a bearer returns 401.
- [ ] Pair a phone via the normal PIN flow, then `GET /api/v1/me` with
      that bearer: shows the `local` admin. `POST /api/v1/logout`
      revokes it — the phone must re-pair.
- [ ] `POST /api/v1/devices` returns a `device_token` once; logging in
      with it via `/api/device-login` works; `DELETE
      /api/v1/devices/{id}` revokes the device AND its sessions (the
      phone stops working until re-paired).
- [ ] Restart the dashboard server: an already-paired phone keeps
      working (device token now served from the persistent registry,
      not just memory).
- [ ] `GET /api/v1/providers`: `key_configured` is a boolean, no key
      material anywhere in the response. POSTing
      `{"provider":"x","api_key":"EVIL"}` to `/api/v1/providers/config`
      returns 400.
- [ ] FREE-FIRST: try to disable every free/local provider or set a
      chain with no free rung — the API must refuse (400). Paid
      providers stay unavailable until `POST
      /api/v1/providers/paid-opt-in` with `{"opt_in": true}`.
- [ ] `GET /api/v1/usage` shows request counters after real provider
      calls; notes honestly state tokens are 0 and Live voice is
      uncounted.
- [ ] HTTPS: with the desktop's `shirazi.crt` trusted on the phone,
      `/api/v1/*` works over HTTPS; the self-signed cert is reused
      across restarts (no re-trust needed).

## 11. Phase 9 hardening (MANUAL)

Automated coverage: 39 hardening tests
(`tests/test_phase9_hardening.py`) + 7 integration tests — all passing in
CI. These items need a real Windows machine:

- [ ] **Fresh install:** on a clean Windows VM, `py setup.py` installs all
      `requirements.txt` entries including the new `keyring` and `plyer`
      declarations; `py main.py` starts without import errors.
- [ ] **Revocation cascade (real restart):** pair a phone, restart the
      desktop, confirm the phone still works (device token now served from
      the persistent registry with its minted channel key); then
      **Revoke devices** → the phone stops working until re-paired, with
      no lingering in-memory bearer.
- [ ] **Window-title sanitiser (real PowerShell):** voice-command a window
      focus with a hostile title (quotes/backticks/`$`/newlines in the
      title) → the focus action succeeds and no PowerShell error or
      injection occurs.
- [ ] **App launcher:** "open <app>" launches the app; a URL opens the
      default browser (via `os.startfile`, no `cmd.exe` window flashes).
- [ ] **`code_helper` confirmation:** asking the assistant to run or build
      code pops the confirmation banner (it did not before Phase 9); an
      `explain` request does not prompt.
- [ ] **Dependency audit result:** `keyring` (OS credential store) and
      `plyer` (notification fallback) are declared in `requirements.txt`
      as of Phase 9; Playwright and tesseract remain optional and degrade
      honestly (browser tools report load errors, vision reports
      "unavailable").

## Sign-off

| Date | Tester | Result |
|------|--------|--------|
|      |        |        |
