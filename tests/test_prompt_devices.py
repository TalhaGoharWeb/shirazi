"""Unit tests for core/prompt.py (configurable system prompt) and core/devices.py."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import prompt as P
from core.tools import build_registry
from core.devices import DeviceManager


class TestPrompt(unittest.TestCase):
    def test_default_is_shirazi_persona(self):
        out = P.build({})
        self.assertIn("SHIRAZI", out)
        self.assertIn("Calm and professional", out)

    def test_language_block(self):
        out = P.build({"language": "ur"})
        self.assertIn("Urdu", out)
        out = P.build({"language": "ur-Latn"})
        self.assertIn("Roman Urdu", out)
        out = P.build({"language": "ar"})
        self.assertIn("Arabic", out)

    def test_personality_selection(self):
        out = P.build({"personality": "concise"})
        self.assertIn("Terse and direct", out)

    def test_custom_personality_dict(self):
        out = P.build({"personality": {"tone": "Speak like a pirate."}})
        self.assertIn("pirate", out)

    def test_modes(self):
        out = P.build({"research_mode": True})
        self.assertIn("RESEARCH MODE is ON", out)
        out = P.build({"developer_mode": True})
        self.assertIn("DEVELOPER MODE is on", out)
        out = P.build({"safe_mode": True})
        self.assertIn("SAFE MODE is on", out)

    def test_shell_policy_present(self):
        out = P.build({})
        self.assertIn("never run shell commands silently", out.lower())

    def test_tool_permissions_block(self):
        reg = build_registry(inline=[
            {"name": "computer_control", "description": "mouse"},
            {"name": "system_status", "description": "info"},
        ])
        out = P.build({}, tools=reg)
        self.assertIn("computer_control", out)
        self.assertIn("confirm", out.lower())

    def test_user_overrides_appended(self):
        out = P.build({"prompt_overrides": "Always greet with salaam."})
        self.assertIn("salaam", out)

    def test_automation_block(self):
        out = P.build({"automation": {"morning_brief": True, "auto_update": False}})
        self.assertIn("morning_brief", out)
        self.assertIn("auto_update", out)

    def test_template_tokens_rendered(self):
        out = P.build({"assistant_name": "TESTBOT"}, platform="Linux",
                      capabilities="- x", limits="- y")
        self.assertIn("TESTBOT", out)
        self.assertNotIn("{assistant_name}", out)
        self.assertNotIn("{platform}", out)

    def test_describe_config(self):
        d = P.describe_config({"language": "ur", "research_mode": True})
        self.assertEqual(d["language_name"], "Urdu")
        self.assertTrue(d["rtl"])
        self.assertTrue(d["research_mode"])

    def test_missing_template_does_not_crash(self):
        # build() with a broken template path falls back to the inline default
        orig = P._TEMPLATE_PATH
        P._TEMPLATE_PATH = Path("/nonexistent/prompt.txt")
        try:
            out = P.build({})
            self.assertIn("SHIRAZI", out)
        finally:
            P._TEMPLATE_PATH = orig


class TestDevices(unittest.TestCase):
    def _mgr(self):
        td = tempfile.TemporaryDirectory()
        path = Path(td.name) / "devices.json"
        path.write_text(json.dumps({"audio": {"input_device": "", "output_device": ""},
                                    "paired_phones": []}), encoding="utf-8")
        mgr = DeviceManager(config_path=path)
        mgr._tmpdir = td  # keep alive
        return mgr

    def test_select_persists(self):
        mgr = self._mgr()
        self.assertTrue(mgr.select_input("USB Mic"))
        self.assertEqual(mgr.selected_input(), "USB Mic")
        # Re-load from disk: persisted
        mgr2 = DeviceManager(config_path=mgr._path)
        self.assertEqual(mgr2.selected_input(), "USB Mic")

    def test_select_unknown_rejected_when_list_known(self):
        mgr = self._mgr()
        mgr._inputs = ["Real Mic"]
        self.assertFalse(mgr.select_input("Fake Mic"))
        self.assertTrue(mgr.select_input("Real Mic"))

    def test_phone_pairing(self):
        mgr = self._mgr()
        mgr.register_phone("tok123", label="Pixel")
        self.assertEqual(len(mgr.paired_phones()), 1)
        self.assertTrue(mgr.unregister_phone("tok123"))
        self.assertEqual(mgr.paired_phones(), [])

    def test_refresh_background_returns_thread(self):
        mgr = self._mgr()
        th = mgr.refresh_background()
        th.join(timeout=10)
        self.assertIsInstance(mgr.summary()["inputs"], list)


if __name__ == "__main__":
    unittest.main()
