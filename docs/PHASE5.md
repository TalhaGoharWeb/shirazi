# Phase 5 — Desktop Experience

Professional PyQt6 desktop experience for SHIRAZI: avatar render modes,
state machine, formant lip-sync, reactor visualisation, professional audio
panel, telemetry, and the start of i18n extraction. Extends `ui.py`,
`core/avatar*.py`, `core/viseme.py`, `core/audio_devices.py` in place —
nothing was rewritten for style.

## What landed

**Avatar & rendering**
- `core/avatar_state.py` — `AvatarState` (IDLE → LISTENING → THINKING →
  SPEAKING → EXECUTING → SUCCESS → ERROR → OFFLINE), thread-safe
  `AvatarStateMachine` with a legal-transition table, strict `transition()`
  and fail-soft `request()`, `state.changed` on the Phase 4 event bus.
  `ui.py`'s `_apply_state()` mirrors every UI state into the machine;
  unknown legacy states (INITIALISING, MUTED) are ignored, never raise.
- `core/avatar_styles.py` — `default` / `kofia` / `turban` / `kufi`
  metadata plus `realistic` / `hologram` / `reactor` render-mode handling.
  `core/avatar.py` gained `set_appearance()` and headwear painting.
- `HudCanvas` gained `set_render_mode()` / `set_avatar_style()`; REALISTIC
  paints the shaded mesh, HOLOGRAM the wireframe, REACTOR embeds the new
  `ui_reactor.ReactorWidget` (72-point ring, 12 spokes, gyro rings, iris,
  rosette, particles, sphere, sweep, telemetry). The legacy `hud_style`
  flag is kept in sync so old configs still work.

**Lip-sync** — `core/formant.py` holds the DSP extracted from `main.py`
(`pcm_level`, `analyze_frames`, ~20 ms hop, F1→openness / F2→width).
`main.py`'s `_pcm_level` / `_pcm_visemes` now delegate to it.
`core/viseme.py` gained Arabic/Urdu-script articulation mapping and
`formant_to_viseme()`. Unwritten short vowels in Arabic/Urdu text still
come from audio formants — documented, not hidden.

**Audio panel** — `AudioDeviceOverlay` rebuilt: input/output pickers with
HOST API / CHANNELS / SAMPLE RATE / STATUS per device (fixed
`describe_devices()` reporting `host_api='n/a'` — it stringified the
host-API dicts before `_host_api_name()` could read them), non-blocking
mic test (VU @ ~30 fps, %, dB, peak-hold, clipping warning, stream always
closed on hide), synthetic 3-tone chime on a worker, background rescan,
mic gain 50–200 % and master volume 0–100 % sliders. `main.py` applies mic
gain before queueing/sending PCM and master volume before playback **and**
before viseme/level analysis, so the mouth, VU and echo guard track the
signal actually sent. Device changes reconnect with `keep_context=True`.

**Telemetry** — `core/telemetry.py` (throttled `TelemetrySampler`: CPU, RAM,
disk, battery, uptime, network; honest "n/a" when a sensor is missing) plus
a `TelemetryOverlay` with eased animated bars.

**i18n** — All four catalogs (`en`, `ur`, `ar`, `ur-Latn`) expanded to
60 keys with enforced parity (`tests/test_phase5_desktop.py`). `ConfirmBanner`
is fully extracted. The settings drawer has a language picker that saves
the choice, calls `i18n.set_language()` and applies `Qt.RightToLeft` for
Urdu/Arabic. Newly extracted strings apply immediately; the rest of the HUD
picks the language up on restart.

## Deliberately not done

- `core.prompt.build()` was **not** adopted in `_build_config()`: it would
  inject persona/permissions/modes/automation blocks the manual assembly
  intentionally omits, and it lacks the address-form identity logic. The
  swap is not semantics-preserving; kept manual.
- PyQt6 rendering and real audio hardware are **manual QA only**
  (`docs/WINDOWS_QA.md`). The sandbox has no PyQt6, no sounddevice, no
  psutil, no WASAPI/MME — nothing here claims to have run on Windows.

## Verification

- `python -m unittest discover tests` — **128 tests, OK** (43 new Phase 5
  tests: state transitions, F1/F2 mapping, reactor params, mocked devices,
  gain/clipping, telemetry, i18n parity).
- Every Python file passes `py_compile`.
