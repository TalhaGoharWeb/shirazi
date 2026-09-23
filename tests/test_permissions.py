"""Unit tests for core/tools + core/permissions — incl. the shell-gate proof."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.permissions import Level, check, configure, default_level_for
from core.permissions import request_shell_execution
from core.tools import build_registry, ToolSpec
from core import confirm


class TestToolRegistry(unittest.TestCase):
    def test_build_from_inline_dicts(self):
        decls = [
            {"name": "system_status", "description": "Reads system info",
             "parameters": {"type": "object", "properties": {}}},
            {"name": "computer_control", "description": "Mouse+keyboard"},
        ]
        reg = build_registry(inline=decls)
        self.assertTrue(reg.has("system_status"))
        spec = reg.get("system_status")
        self.assertEqual(spec.category, "system")
        self.assertEqual(spec.permission, Level.SAFE)
        self.assertEqual(spec.source, "inline")
        cc = reg.get("computer_control")
        self.assertEqual(cc.permission, Level.USER_CONFIRMATION)
        self.assertEqual(cc.category, "computer")

    def test_bad_declaration_skipped(self):
        reg = build_registry(inline=[{"no_name": True}, {"name": "ok_tool"}])
        self.assertTrue(reg.has("ok_tool"))
        self.assertFalse(reg.has(""))

    def test_unknown_tool_defaults(self):
        reg = build_registry(inline=[{"name": "brand_new_tool"}])
        spec = reg.get("brand_new_tool")
        self.assertEqual(spec.category, "utility")
        # fail-closed: unknown tools need confirmation
        self.assertEqual(spec.permission, Level.USER_CONFIRMATION)

    def test_list_and_summary(self):
        reg = build_registry(inline=[
            {"name": "web_search"}, {"name": "system_status"}])
        self.assertEqual(len(reg.list_by_category("research")), 1)
        self.assertEqual(len(reg.list_by_permission(Level.SAFE)), 1)
        s = reg.summary()
        self.assertEqual(s["total"], 2)
        prompt = reg.describe_for_prompt()
        self.assertIn("web_search", prompt)


class TestPermissions(unittest.TestCase):
    def setUp(self):
        configure({})

    def tearDown(self):
        configure({})

    def test_safe_tools_run_free(self):
        r = check("system_status")
        self.assertTrue(r.allowed)
        self.assertFalse(r.needs_confirmation)

    def test_read_only_runs_free(self):
        r = check("web_search")
        self.assertTrue(r.allowed)
        self.assertFalse(r.needs_confirmation)

    def test_powerful_tools_need_confirmation(self):
        for tool in ("computer_control", "file_controller", "dev_agent",
                     "browser_control", "send_message", "open_app",
                     "shutdown_shirazi", "computer_settings"):
            r = check(tool)
            self.assertTrue(r.allowed, tool)
            self.assertTrue(r.needs_confirmation, tool)

    def test_unknown_tool_fails_closed(self):
        r = check("some_tool_nobody_declared")
        self.assertTrue(r.allowed)  # allowed but never silent
        self.assertTrue(r.needs_confirmation)

    def test_sensitive_subaction_raises_level(self):
        r = check("file_controller", {"action": "delete", "path": "/tmp/x"})
        self.assertTrue(r.needs_confirmation)
        self.assertIn("delet", r.reason.lower())

    def test_privileged_denied_without_developer_mode(self):
        from core import permissions as P
        P._DEFAULT_LEVELS["__test_priv"] = Level.PRIVILEGED
        try:
            r = check("__test_priv")
            self.assertFalse(r.allowed)
            self.assertFalse(r.needs_confirmation)
        finally:
            del P._DEFAULT_LEVELS["__test_priv"]

    def test_privileged_still_needs_confirmation_with_developer_mode(self):
        from core import permissions as P
        P._DEFAULT_LEVELS["__test_priv"] = Level.PRIVILEGED
        configure({"developer_mode": True})
        try:
            r = check("__test_priv")
            self.assertTrue(r.allowed)
            self.assertTrue(r.needs_confirmation)  # never silent
        finally:
            del P._DEFAULT_LEVELS["__test_priv"]

    def test_safe_mode_confirms_reads(self):
        configure({"safe_mode": True})
        r = check("web_search")
        self.assertTrue(r.allowed)
        self.assertTrue(r.needs_confirmation)

    def test_default_level_for(self):
        self.assertEqual(default_level_for("system_status"), Level.SAFE)
        self.assertEqual(default_level_for("nope_unknown"), Level.USER_CONFIRMATION)


class TestShellGate(unittest.TestCase):
    """PROOF: no LLM-generated command string executes without the human gate."""

    def setUp(self):
        # Simulate "no interface bound" (headless / early boot).
        self._old_show = confirm._show_cb
        confirm._show_cb = None

    def tearDown(self):
        confirm._show_cb = self._old_show

    def test_shell_refused_without_interface(self):
        ran = []
        msg = request_shell_execution(
            "rm -rf /tmp/evil", run=lambda: ran.append(True) or "done")
        self.assertEqual(ran, [])  # the command did NOT run
        self.assertIn("not available", msg)
        self.assertIn("have not done it", msg)

    def test_shell_parks_behind_banner_when_bound(self):
        ran = []
        shown = []

        def fake_show(title, detail):
            shown.append((title, detail))

        confirm._show_cb = fake_show
        confirm._hide_cb = lambda: None
        msg = request_shell_execution(
            "echo hello", run=lambda: ran.append(True) or "done",
            context="test")
        try:
            self.assertEqual(ran, [])  # parked, not executed
            self.assertEqual(len(shown), 1)
            self.assertIn("echo hello", shown[0][1])  # verbatim on the banner
            self.assertIn("CONFIRMATION_PENDING", msg)
            # Now the user presses CONFIRM:
            confirm.resolve(True)
            import time
            for _ in range(100):
                if ran:
                    break
                time.sleep(0.05)
            self.assertEqual(ran, [True])  # ran only after human confirm
        finally:
            confirm._show_cb = None
            confirm._hide_cb = None

    def test_shell_cancel_never_runs(self):
        ran = []
        confirm._show_cb = lambda t, d: None
        confirm._hide_cb = lambda: None
        try:
            request_shell_execution("echo hi", run=lambda: ran.append(True))
            confirm.resolve(False)  # user pressed CANCEL
            import time
            time.sleep(0.3)
            self.assertEqual(ran, [])
        finally:
            confirm._show_cb = None
            confirm._hide_cb = None

    def test_empty_command_refused(self):
        msg = request_shell_execution("   ", run=lambda: "x")
        self.assertIn("nothing was done", msg.lower())


if __name__ == "__main__":
    unittest.main()
