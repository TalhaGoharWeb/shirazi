"""Phase 5 desktop-experience tests (headless; PyQt6/audio hardware mocked).

Covers: avatar state machine transitions, formant F1/F2 → openness/width
mapping, reactor parameter behaviour, mocked audio-device metadata + rescan,
mic gain / soft clipping / master volume, telemetry formatting + throttling,
and i18n catalog parity.

Everything here runs on plain CPython + numpy. Nothing touches Qt widgets or
real audio hardware — the Windows render/device QA is MANUAL in
docs/WINDOWS_QA.md.
"""

import sys
import types
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── avatar state machine ──────────────────────────────────────────────────
from core.avatar_state import (
    AvatarState, AvatarStateMachine, IllegalTransition, animation_for,
)


class TestAvatarStateMachine(unittest.TestCase):
    def test_initial_state_idle(self):
        self.assertEqual(AvatarStateMachine().state, AvatarState.IDLE)

    def test_full_happy_path(self):
        sm = AvatarStateMachine()
        for nxt in (AvatarState.LISTENING, AvatarState.THINKING,
                    AvatarState.SPEAKING, AvatarState.EXECUTING,
                    AvatarState.SUCCESS, AvatarState.IDLE):
            sm.transition(nxt)
        self.assertEqual(sm.state, AvatarState.IDLE)

    def test_error_path_and_recovery(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.LISTENING)
        sm.transition(AvatarState.ERROR)
        self.assertEqual(sm.state, AvatarState.ERROR)
        sm.transition(AvatarState.IDLE)
        sm.transition(AvatarState.OFFLINE)
        sm.transition(AvatarState.IDLE)
        self.assertEqual(sm.state, AvatarState.IDLE)

    def test_illegal_transition_raises(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.LISTENING)
        with self.assertRaises(IllegalTransition):
            sm.transition(AvatarState.SPEAKING)  # LISTENING -> SPEAKING illegal

    def test_request_is_fail_soft(self):
        sm = AvatarStateMachine()
        self.assertTrue(sm.request(AvatarState.LISTENING))
        self.assertFalse(sm.request(AvatarState.SUCCESS))  # illegal
        self.assertEqual(sm.state, AvatarState.LISTENING)  # unchanged

    def test_can(self):
        sm = AvatarStateMachine()
        self.assertTrue(sm.can(AvatarState.LISTENING))
        self.assertTrue(sm.can(AvatarState.OFFLINE))   # OFFLINE from anywhere
        self.assertFalse(sm.can(AvatarState.IDLE))     # no self-transition

    def test_history(self):
        sm = AvatarStateMachine()
        sm.transition(AvatarState.LISTENING)
        sm.transition(AvatarState.THINKING)
        h = sm.history()
        self.assertIn("IDLE", h[0])
        self.assertIn("THINKING", h[-1])

    def test_animation_map_covers_all_states(self):
        for st in AvatarState:
            a = animation_for(st)
            self.assertIn("breath_hz", a)
            self.assertIn("motion", a)
            self.assertIn("glow", a)


# ── formant analysis ──────────────────────────────────────────────────────
from core.formant import pcm_level, analyze_frames
from core.viseme import formant_to_viseme


def _tone(freq, sr, secs, amp=9000.0):
    n = int(sr * secs)
    return (np.sin(2 * np.pi * freq * np.arange(n) / sr) * amp).astype(np.int16)


class TestFormant(unittest.TestCase):
    SR = 24000

    def test_hop_is_approx_20ms(self):
        # 50 frames per second at 24 kHz => 480 samples per hop.
        frames = analyze_frames(_tone(440, self.SR, 1.0), sr=self.SR)
        self.assertEqual(len(frames), 50)

    def test_silence_gives_closed_frames(self):
        frames = analyze_frames(np.zeros(self.SR, dtype=np.int16), sr=self.SR)
        self.assertTrue(frames)
        for level, openness, width in frames:
            self.assertEqual((level, openness, width), (0.0, 0.0, 0.0))

    def test_empty_input(self):
        self.assertEqual(analyze_frames(np.array([], dtype=np.int16),
                                        sr=self.SR), [])
        self.assertEqual(pcm_level(np.array([], dtype=np.int16)), 0.0)

    def test_f1_openness_mapping(self):
        # Energy concentrated in the high F1 band (450-1100 Hz) reads open;
        # energy in the low F1 band (150-450 Hz) reads closed.
        open_t = _tone(700, self.SR, 0.5)
        closed_t = _tone(250, self.SR, 0.5)
        o_open = np.mean([f[1] for f in analyze_frames(open_t, sr=self.SR)
                          if f[0] > 0])
        o_closed = np.mean([f[1] for f in analyze_frames(closed_t, sr=self.SR)
                            if f[0] > 0])
        self.assertGreater(o_open, o_closed)

    def test_f2_width_mapping(self):
        # High F2 energy (1700-3200 Hz) reads spread; low F2 (600-1300 Hz)
        # reads rounded.
        spread = _tone(2400, self.SR, 0.5)
        rounded = _tone(800, self.SR, 0.5)
        w_spread = np.mean([f[2] for f in analyze_frames(spread, sr=self.SR)
                            if f[0] > 0])
        w_rounded = np.mean([f[2] for f in analyze_frames(rounded, sr=self.SR)
                             if f[0] > 0])
        self.assertGreater(w_spread, w_rounded)

    def test_formant_to_viseme_shapes(self):
        self.assertEqual(formant_to_viseme(0.9, 0.0), "AA")     # open
        self.assertEqual(formant_to_viseme(0.2, 0.9), "I")      # spread
        self.assertEqual(formant_to_viseme(0.2, -0.9), "U")     # rounded
        self.assertEqual(formant_to_viseme(0.02, 0.0), "MBP")   # closed
        self.assertEqual(formant_to_viseme(0.5, 0.0, level=0.0), "REST")
        self.assertEqual(formant_to_viseme("bad", None), "REST")

    def test_level_scaling(self):
        loud = _tone(440, self.SR, 0.3, amp=12000.0)
        quiet = _tone(440, self.SR, 0.3, amp=300.0)
        self.assertGreater(pcm_level(loud), pcm_level(quiet))
        self.assertLessEqual(pcm_level(loud), 1.0)


# ── reactor parameters ────────────────────────────────────────────────────
from core.reactor import compute as reactor_compute


class TestReactor(unittest.TestCase):
    def test_frame_shape(self):
        p = reactor_compute(0.5, state=AvatarState.IDLE, t=1.0)
        self.assertEqual(len(p.waveform_ring), 72)
        self.assertEqual(len(p.spokes), 12)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in p.waveform_ring))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in p.spokes))

    def test_audio_drives_visuals(self):
        quiet = reactor_compute(0.0, state=AvatarState.SPEAKING, t=2.0)
        loud = reactor_compute(1.0, state=AvatarState.SPEAKING, t=2.0)
        self.assertGreater(loud.sphere_scale, quiet.sphere_scale)
        self.assertGreater(loud.glow, quiet.glow)
        self.assertGreater(max(loud.spokes), max(quiet.spokes))

    def test_state_bias_differs(self):
        idle = reactor_compute(0.5, state=AvatarState.IDLE, t=3.0)
        err = reactor_compute(0.5, state=AvatarState.ERROR, t=3.0)
        self.assertNotEqual(idle.particle_speed, err.particle_speed)
        self.assertGreater(idle.glow, err.glow)   # ERROR damps the core

    def test_offline_is_dim(self):
        off = reactor_compute(0.5, state=AvatarState.OFFLINE, t=1.0)
        on = reactor_compute(0.5, state=AvatarState.SPEAKING, t=1.0)
        self.assertLess(off.glow, on.glow)
        self.assertLess(off.sphere_scale, on.sphere_scale)

    def test_angles_advance_with_time(self):
        a = reactor_compute(0.5, t=1.0)
        b = reactor_compute(0.5, t=2.0)
        self.assertNotEqual(a.sweep_angle, b.sweep_angle)
        self.assertNotEqual(a.particle_phase, b.particle_phase)

    def test_level_clamped(self):
        p = reactor_compute(99.0, t=0.0)
        self.assertLessEqual(p.glow, 1.0)
        self.assertLessEqual(p.sphere_scale, 2.0)


# ── audio devices (mocked sounddevice) ────────────────────────────────────
def _install_fake_sounddevice():
    fake = types.ModuleType("sounddevice")

    _devs = [
        {"name": "Built-in Microphone", "max_input_channels": 1,
         "max_output_channels": 0, "default_samplerate": 44100.0,
         "hostapi": 0},
        {"name": "USB Headset", "max_input_channels": 1,
         "max_output_channels": 2, "default_samplerate": 48000.0,
         "hostapi": 1},
        {"name": "Speakers (Realtek)", "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 48000.0,
         "hostapi": 1},
    ]
    _apis = [{"name": "MME"}, {"name": "WASAPI"}]

    fake.query_devices = lambda *a: ([dict(d, index=i)
                                      for i, d in enumerate(_devs)]
                                     if not a else dict(_devs[a[0]],
                                                        index=a[0]))
    fake.query_hostapis = lambda *a: ([dict(x, index=i)
                                       for i, x in enumerate(_apis)]
                                      if not a else _apis[a[0]])
    fake.default = types.SimpleNamespace(device=(0, 2))

    class _Stream:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

        def write(self, data):
            pass  # probe writes silence; the fake swallows it

        def read(self, n):
            return (np.zeros((n, 1), dtype=np.int16), False)

    fake.InputStream = _Stream
    fake.RawOutputStream = _Stream
    sys.modules["sounddevice"] = fake
    return fake


class TestAudioDevices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_fake_sounddevice()
        import core.audio_devices as ad
        ad.prefetch = lambda *a, **k: None  # no threads in tests
        # The transport probe measures real hardware timing; mock it at the
        # probe boundary so the rest of the metadata path is tested for real.
        cls._orig_probe = ad._transport_works
        ad._transport_works = lambda idx, kind, api_key: True
        cls.ad = ad

    @classmethod
    def tearDownClass(cls):
        cls.ad._transport_works = cls._orig_probe

    def test_list_devices_split_by_direction(self):
        ad = self.ad
        self.assertIn("Built-in Microphone", ad.list_devices("input"))
        self.assertIn("USB Headset", ad.list_devices("input"))
        self.assertIn("USB Headset", ad.list_devices("output"))
        self.assertIn("Speakers (Realtek)", ad.list_devices("output"))
        self.assertNotIn("Speakers (Realtek)", ad.list_devices("input"))

    def test_describe_fields(self):
        rows = self.ad.describe_devices("output")
        spk = next(r for r in rows if r["name"] == "Speakers (Realtek)")
        self.assertEqual(spk["host_api"], "WASAPI")
        self.assertEqual(spk["channels"], 2)
        # sample_rate = the app's configured rate; default_rate = the
        # device's own reported rate.
        self.assertEqual(spk["default_rate"], 48000)
        self.assertEqual(spk["status"], "READY")

    def test_status_line_format(self):
        line = self.ad.status_line("USB Headset", "output")
        self.assertIn("HOST API", line)
        self.assertIn("CHANNELS", line)
        self.assertIn("SAMPLE RATE", line)
        self.assertIn("STATUS", line)

    def test_rescan_returns_both_directions(self):
        out = self.ad.rescan()
        self.assertIn("input", out)
        self.assertIn("output", out)
        self.assertTrue(out["input"])
        self.assertTrue(out["output"])

    def test_resolve_default(self):
        self.assertIsNone(self.ad.resolve("", "input"))
        self.assertIsNone(self.ad.resolve("no such device", "input"))

    def test_device_detail_unknown(self):
        d = self.ad.device_detail("no such device", "input")
        self.assertIn("NOT CONNECTED", d["status"])


# ── audio gain / volume ───────────────────────────────────────────────────
from core.audio_gain import (apply_mic_gain, apply_master_volume, clamp_gain,
                             clamp_volume, percent_to_db)


class TestAudioGain(unittest.TestCase):
    PCM = np.array([1000, 30000, -500, 32000, -32000, 2000], dtype=np.int16)

    def test_clamps(self):
        self.assertEqual(clamp_gain(20), 50)
        self.assertEqual(clamp_gain(500), 200)
        self.assertEqual(clamp_volume(-5), 0)
        self.assertEqual(clamp_volume(140), 100)

    def test_partial_overload_does_not_crash(self):
        # The old masked np.where() broadcast-crashed here; the exception
        # path then returned the block unchanged (gain silently lost).
        out = apply_mic_gain(self.PCM, 200)
        self.assertEqual(out.dtype, np.int16)
        self.assertTrue((out <= 32767).all())
        # Quiet samples double; overloaded ones saturate at full scale.
        self.assertEqual(out[0], 2000)
        self.assertEqual(out[1], 32767)

    def test_unity_keeps_quiet_samples_bit_linear(self):
        out = apply_mic_gain(np.array([100, -200, 500], dtype=np.int16), 100)
        self.assertTrue((out == np.array([100, -200, 500])).all())

    def test_never_wraps_on_hot_input(self):
        hot = np.full(64, 32000, dtype=np.int16)
        out = apply_mic_gain(hot, 200)
        self.assertTrue((out >= 0).all())   # no int16 wraparound to negative

    def test_master_volume(self):
        self.assertTrue((apply_master_volume(self.PCM, 100) == self.PCM).all())
        self.assertTrue((apply_master_volume(self.PCM, 0) == 0).all())
        half = apply_master_volume(
            np.array([1000, -2000], dtype=np.int16), 50)
        self.assertTrue((half == np.array([500, -1000])).all())

    def test_db_math(self):
        self.assertAlmostEqual(percent_to_db(100), 0.0)
        self.assertAlmostEqual(percent_to_db(200), 6.0206, places=3)
        self.assertAlmostEqual(percent_to_db(50), -6.0206, places=3)

    def test_bad_input_passes_through(self):
        self.assertIs(apply_mic_gain(None, 150), None)


# ── telemetry ─────────────────────────────────────────────────────────────
from core.telemetry import TelemetrySampler, format_uptime, clamp_bar


class TestTelemetry(unittest.TestCase):
    def test_format_uptime(self):
        # '3d 4h 12m' — days only when non-zero, always minutes.
        self.assertEqual(format_uptime(0), "0m")
        self.assertEqual(format_uptime(90), "1m")
        self.assertEqual(format_uptime(3661), "1h 1m")
        self.assertEqual(format_uptime(90061), "1d 1h 1m")

    def test_clamp_bar(self):
        self.assertEqual(clamp_bar(-3), 0.0)
        self.assertEqual(clamp_bar(150), 100.0)
        self.assertEqual(clamp_bar(40), 40.0)

    def test_snapshot_rows_shape(self):
        s = TelemetrySampler(min_interval=60.0).snapshot()
        rows = s.as_rows()
        keys = [r[0] for r in rows]
        self.assertEqual(keys, ["CPU", "RAM", "DISK", "BATTERY",
                                "UPTIME", "NETWORK"])
        for key, value, display in rows:
            self.assertIsInstance(display, str)
            self.assertTrue(display)   # never a blank readout

    def test_throttling(self):
        # Second snapshot inside min_interval reuses the cached sample.
        sampler = TelemetrySampler(min_interval=600.0)
        a = sampler.snapshot()
        b = sampler.snapshot()
        self.assertIs(a, b)

    def test_battery_missing_is_honest(self):
        # Sandbox has no psutil and no battery: the row must say so, not 0%.
        s = TelemetrySampler(min_interval=60.0).snapshot()
        rows = dict((k, d) for k, v, d in s.as_rows())
        self.assertNotEqual(rows["BATTERY"], "0%")


# ── i18n parity ───────────────────────────────────────────────────────────
import i18n
from i18n import t, set_language, is_rtl


class TestI18nParity(unittest.TestCase):
    PHASE5_KEYS = [
        "confirm_banner_title", "confirm_button", "cancel_button",
        "audio_title", "audio_input_label", "audio_output_label",
        "audio_test_mic", "audio_stop", "audio_test_chime", "audio_rescan",
        "audio_apply", "audio_close", "audio_mic_gain", "audio_master_volume",
        "audio_clipping", "audio_peak", "audio_reconnect_note",
        "audio_host_api", "audio_channels", "audio_sample_rate",
        "audio_status",
        "telemetry_title",
        "avatar_style_label", "render_mode_label", "language_label",
        "style_default", "style_kofia", "style_turban", "style_kufi",
        "mode_realistic", "mode_hologram", "mode_reactor",
        "state_idle", "state_listening", "state_thinking", "state_speaking",
        "state_executing", "state_success", "state_error", "state_offline",
        "status_ready", "status_unavailable", "status_not_connected",
        "status_system_default",
    ]

    def tearDown(self):
        set_language("en")

    def test_catalog_parity(self):
        import json
        base = Path(__file__).resolve().parent.parent / "i18n"
        keysets = {}
        for lang in ("en", "ur", "ar", "ur-Latn"):
            with open(base / f"{lang}.json", encoding="utf-8") as f:
                keysets[lang] = set(json.load(f)["strings"].keys())
        for lang, keys in keysets.items():
            self.assertEqual(keys, keysets["en"],
                             f"catalog {lang} diverges from en")
        self.assertEqual(len(keysets["en"]), 60)

    def test_phase5_keys_present_everywhere(self):
        for lang in ("en", "ur", "ar", "ur-Latn"):
            set_language(lang)
            for key in self.PHASE5_KEYS:
                val = t(key)
                self.assertNotEqual(val, key, f"{lang}.{key} missing")

    def test_translations_are_not_english_copies(self):
        set_language("ur")
        self.assertNotEqual(t("audio_title"), "Audio Devices")
        set_language("ar")
        self.assertNotEqual(t("audio_title"), "Audio Devices")

    def test_rtl_flags(self):
        for lang, rtl in (("en", False), ("ur", True),
                          ("ar", True), ("ur-Latn", False)):
            set_language(lang)
            self.assertEqual(is_rtl(), rtl, lang)


if __name__ == "__main__":
    unittest.main()
