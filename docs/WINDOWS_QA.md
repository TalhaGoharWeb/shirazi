# Windows QA — Phase 5 Desktop Experience (MANUAL)

Everything in this file must be checked by a human on a real Windows machine.
The Linux sandbox has no PyQt6, no WASAPI/MME/DirectSound and no audio
hardware, so none of the items below can be verified here — the automated
suite (`python -m unittest discover tests`, 128 tests) covers only the
headless logic. Do not mark an item done until you have seen it with your
own eyes.

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

## Sign-off

| Date | Tester | Result |
|------|--------|--------|
|      |        |        |
