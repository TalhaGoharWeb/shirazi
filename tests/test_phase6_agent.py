"""Tests for the Phase-6 agent engine: intent → plan → gated execution.

Zero network by design: providers are mocked, search/fetch functions are
injected, and every external-dependency probe asserts the *honest*
unavailability path (Playwright/tesseract/psutil absent in this sandbox).
"""

import asyncio
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import agent
from core.agent import (Intent, detect_intent, build_plan, create_agent,
                        AgentStatus, ResearchAgent, Citation, Vision,
                        shell_run)
from core.agent import tools_browser
from core import events, logger
from core.permissions import Level, check, configure
from core.tools import build_registry


class MockProvider:
    """Stands in for the AI provider chain (Desktop→AI, mocked)."""

    def __init__(self, text="Mocked answer."):
        self.text = text
        self.calls = []

    def generate(self, prompt, *, system="", timeout_s=30.0, json_mode=False):
        self.calls.append(prompt)
        return self.text


# ── 1. Intent routing ─────────────────────────────────────────────────────

class TestIntent(unittest.TestCase):
    def test_compound_chrome_weather(self):
        r = detect_intent("Open Chrome and search for today's weather")
        self.assertEqual(r.intent, Intent.COMPOUND)
        self.assertEqual([s.intent for s in r.sub_intents],
                         [Intent.APP_OPEN, Intent.WEB_SEARCH])
        self.assertEqual(r.sub_intents[0].slots.get("app"), "chrome")
        self.assertIn("weather", r.sub_intents[1].slots.get("query", ""))

    def test_find_pdf_downloads(self):
        r = detect_intent("Find the PDF I downloaded yesterday")
        self.assertEqual(r.intent, Intent.FILE_FIND)
        self.assertEqual(r.slots.get("extension"), ".pdf")
        self.assertEqual(r.slots.get("path"), "downloads")
        self.assertEqual(r.slots.get("recency"), "yesterday")

    def test_open_project_folder(self):
        r = detect_intent("Open my project folder")
        self.assertEqual(r.intent, Intent.FOLDER_OPEN)

    def test_screen_summarize(self):
        r = detect_intent("Summarize what's currently on my screen")
        self.assertEqual(r.intent, Intent.SCREEN_SUMMARIZE)

    def test_volume_down(self):
        r = detect_intent("Turn the volume down")
        self.assertEqual(r.intent, Intent.VOLUME)
        self.assertEqual(r.slots.get("op"), "down")

    def test_search_documents(self):
        r = detect_intent("Search my documents for this topic")
        self.assertEqual(r.intent, Intent.FILE_FIND)
        self.assertEqual(r.slots.get("path"), "documents")

    def test_shell(self):
        r = detect_intent("run the command ls -la in the terminal")
        self.assertEqual(r.intent, Intent.SHELL)
        self.assertIn("ls -la", r.slots.get("command", ""))

    def test_unknown(self):
        r = detect_intent("xyzzy plugh qwerty asdf")
        self.assertEqual(r.intent, Intent.UNKNOWN)
        self.assertFalse(r)


# ── 2. Planner: explainable multi-step plans ───────────────────────────────

class TestPlanner(unittest.TestCase):
    def test_compound_plan_is_explainable(self):
        intent = detect_intent("Open Chrome and search for today's weather")
        plan = build_plan(intent)
        tools = [s.tool for s in plan.steps]
        self.assertEqual(tools, ["app_open", "browser_search"])
        for s in plan.steps:
            self.assertTrue(s.why, f"step {s.tool} has no explanation")
        text = plan.explain()
        self.assertIn("app_open", text)
        self.assertIn("browser_search", text)
        self.assertIn("1.", text)
        self.assertIn("2.", text)

    def test_volume_plan(self):
        plan = build_plan(detect_intent("Turn the volume down"))
        self.assertEqual([s.tool for s in plan.steps], ["volume_down"])

    def test_screen_plan(self):
        plan = build_plan(detect_intent("Summarize what's currently on my screen"))
        self.assertEqual([s.tool for s in plan.steps], ["vision_describe"])

    def test_file_find_plan(self):
        plan = build_plan(detect_intent("Find the PDF I downloaded yesterday"))
        self.assertEqual(plan.steps[0].tool, "search_files")
        self.assertEqual(plan.steps[0].args.get("extension"), ".pdf")

    def test_missing_tool_degrades_honestly(self):
        plan = build_plan(detect_intent("Turn the volume down"),
                          available={"some_other_tool"})
        self.assertEqual(len(plan.steps), 1)
        self.assertTrue(plan.steps[0].skipped)
        self.assertEqual(plan.runnable(), [])


# ── 3. Permission gating of dangerous tools ───────────────────────────────

class TestPermissionGating(unittest.TestCase):
    def setUp(self):
        configure({})  # default flags: no developer mode, no safe mode

    def test_dangerous_tools_need_confirmation(self):
        for name in ("delete_file", "move_file", "rename_file", "copy_file",
                     "create_file", "file_organize", "shell_run",
                     "browser_click", "browser_type", "keyboard_hotkey",
                     "mouse_click", "app_open", "send_message"):
            gate = check(name, {})
            self.assertTrue(gate.needs_confirmation,
                            f"{name} must need confirmation")
            self.assertTrue(gate.allowed)

    def test_reads_are_free(self):
        for name, level in (("mouse_move", Level.SAFE),
                            ("system_stats", Level.SAFE),
                            ("notify", Level.SAFE),
                            ("search_files", Level.READ_ONLY),
                            ("browser_search", Level.READ_ONLY)):
            self.assertEqual(check(name, {}).level, level, name)
            self.assertFalse(check(name, {}).needs_confirmation, name)

    def test_unknown_tool_fails_closed(self):
        gate = check("brand_new_never_seen_tool", {})
        self.assertTrue(gate.needs_confirmation)

    def test_agent_parks_without_confirm_hook(self):
        """No hook → PENDING_CONFIRMATION, and the tool NEVER runs."""
        eng = create_agent(provider=MockProvider())
        calls = []
        orig = eng._dispatch["delete_file"]
        eng._dispatch["delete_file"] = lambda p: calls.append(p) or orig(p)
        try:
            res = eng.run_sync("delete the file notes.txt in my home folder",
                               timeout=60)
        finally:
            eng.close()
        self.assertEqual(res.status, AgentStatus.PENDING_CONFIRMATION)
        self.assertEqual(res.steps_executed, 0)
        self.assertEqual(calls, [])
        self.assertIsNotNone(res.pending)
        self.assertEqual(res.pending["tool"], "delete_file")

    def test_confirm_hook_yes_runs_no_cancels(self):
        ran = []
        eng = create_agent(provider=MockProvider(),
                           confirm_hook=lambda t, a, r: True)
        eng._dispatch["notify"] = lambda p: ran.append(p) or "ok"
        try:
            res = eng.run_sync("show a notification", timeout=60)
        finally:
            eng.close()
        # notify is SAFE — runs without any hook at all
        self.assertEqual(res.status, AgentStatus.COMPLETED)
        self.assertEqual(len(ran), 1)

        ran2 = []
        eng2 = create_agent(provider=MockProvider(),
                            confirm_hook=lambda t, a, r: False)
        eng2._dispatch["delete_file"] = lambda p: ran2.append(p) or "ok"
        try:
            res2 = eng2.run_sync("delete the file notes.txt", timeout=60)
        finally:
            eng2.close()
        self.assertEqual(res2.status, AgentStatus.CANCELLED)
        self.assertEqual(ran2, [])

    def test_shell_never_runs_silently(self):
        """The only shell path refuses without a human gate — proven by a
        marker file that must NOT appear."""
        marker = Path.home() / ".shirazi_agent_test_shell_marker"
        if marker.exists():
            marker.unlink()
        out = shell_run({"command": f"touch {marker}"})
        self.assertIn("cannot confirm", out.lower())
        self.assertFalse(marker.exists(),
                         "shell executed without confirmation — HARD RULE VIOLATION")

    def test_shell_has_no_other_path(self):
        """Static proof: executor.py contains no subprocess; shell.py routes
        every execution through request_shell_execution()."""
        base = Path(__file__).resolve().parent.parent / "core" / "agent"
        executor_src = (base / "executor.py").read_text(encoding="utf-8")
        self.assertNotIn("subprocess", executor_src)
        self.assertNotIn("os.system", executor_src)
        shell_src = (base / "shell.py").read_text(encoding="utf-8")
        self.assertIn("request_shell_execution", shell_src)
        # the actual runner is only ever invoked inside the gated callback
        self.assertEqual(shell_src.count("subprocess.run"), 1)


# ── 4. Research: FACT / SOURCE / INFERENCE / UNCERTAINTY ──────────────────

def _cites():
    return [
        Citation("Tower facts A", "https://a.example/tower",
                 snippet="The Eiffel Tower is 330 metres tall and was completed in 1889."),
        Citation("Tower facts B", "https://b.example/tower",
                 snippet="Completed in 1889, the Eiffel Tower stands 330 metres tall in Paris."),
        Citation("Tower myth C", "https://c.example/tower",
                 snippet="The Eiffel Tower completed in 1889 is not 330 metres tall."),
    ]


class TestResearch(unittest.TestCase):
    def test_fact_source_uncertainty_labels(self):
        ra = ResearchAgent(search_fn=lambda q, n: _cites(),
                           fetch_fn=lambda u: "")
        report = ra.run("Eiffel Tower height")
        by_label = {}
        for c in report.claims:
            by_label.setdefault(c.label, []).append(c)
        self.assertIn("FACT", by_label)        # corroborated by a.example + b.example
        self.assertIn("SOURCE", by_label)      # first-seen single source
        self.assertIn("UNCERTAINTY", by_label)  # negation conflict with c.example
        fact = by_label["FACT"][0]
        self.assertGreaterEqual(len({s.domain for s in fact.sources}), 2)
        unc = by_label["UNCERTAINTY"][0]
        self.assertIn("disagree", unc.note)

    def test_no_fabricated_citations(self):
        ra = ResearchAgent(search_fn=lambda q, n: _cites(),
                           fetch_fn=lambda u: "")
        report = ra.run("Eiffel Tower height")
        text = report.render()
        for url in ("https://a.example/tower", "https://b.example/tower",
                    "https://c.example/tower"):
            self.assertIn(url, text)
        # every http URL in the report is one we injected
        found = set(__import__("re").findall(r"https?://[^\s)]+", text))
        injected = {"https://a.example/tower", "https://b.example/tower",
                    "https://c.example/tower"}
        self.assertTrue(found <= injected, f"unaccounted URLs: {found - injected}")

    def test_zero_sources_is_honest(self):
        ra = ResearchAgent(search_fn=lambda q, n: [], fetch_fn=lambda u: "")
        report = ra.run("something obscure")
        self.assertFalse(report.ok)
        self.assertTrue(all(c.label == "UNCERTAINTY" for c in report.claims))
        self.assertIn("No sources could be retrieved", report.render())

    def test_inference_labelled(self):
        ra = ResearchAgent(search_fn=lambda q, n: _cites(),
                           fetch_fn=lambda u: "")
        report = ra.run("Eiffel Tower")
        claim = ra.add_inference(report, "The tower is a major tourist draw.",
                                 based_on=report.citations[:1])
        self.assertEqual(claim.label, "INFERENCE")
        self.assertIn("INFERENCE", report.render())


# ── 5. Agent loop: Desktop→AI (mocked), Desktop→Tools, AI→Tool engine ─────

class TestAgentLoop(unittest.TestCase):
    def test_conversation_uses_mocked_provider(self):
        eng = create_agent(provider=MockProvider("Hello from the mock."))
        try:
            res = eng.run_sync("hello", timeout=60)
        finally:
            eng.close()
        self.assertEqual(res.status, AgentStatus.COMPLETED)
        self.assertEqual(res.answer, "Hello from the mock.")

    def test_unknown_asks_for_clarification(self):
        eng = create_agent(provider=MockProvider("Mocked."))
        try:
            res = eng.run_sync("xyzzy plugh qwerty asdf", timeout=60)
        finally:
            eng.close()
        self.assertEqual(res.status, AgentStatus.NEEDS_INPUT)

    def test_loop_runs_tools_and_reasons(self):
        """AI→Tool engine: the plan's tools are dispatched, observations are
        recorded, and the final answer comes from the provider."""
        seen = []

        def fake_volume(params):
            seen.append(params)
            return "Volume lowered to 40%."

        eng = create_agent(provider=MockProvider("Done — volume is now 40%."),
                           confirm_hook=lambda t, a, r: True)
        eng._dispatch["volume_down"] = fake_volume
        events.reset()
        got = []
        events.subscribe("agent.plan", lambda p: got.append(("plan", p)))
        events.subscribe("agent.step", lambda p: got.append(("step", p)))
        events.subscribe("agent.done", lambda p: got.append(("done", p)))
        try:
            res = eng.run_sync("Turn the volume down", timeout=60)
        finally:
            eng.close()
            events.reset()
        self.assertEqual(res.status, AgentStatus.COMPLETED)
        self.assertEqual(res.steps_executed, 1)
        self.assertEqual(len(seen), 1)
        self.assertEqual(res.observations[0]["tool"], "volume_down")
        self.assertIn("40%", res.observations[0]["observation"])
        self.assertEqual(res.answer, "Done — volume is now 40%.")
        kinds = [k for k, _ in got]
        self.assertEqual(kinds, ["plan", "step", "done"])
        # every planned tool resolves to a real handler (AI→Tool engine)
        for step in res.plan.steps:
            self.assertTrue(eng.handles(step.tool))
            self.assertTrue(step.why)

    def test_run_in_background_returns_future(self):
        eng = create_agent(provider=MockProvider("bg"))
        try:
            fut = eng.run_in_background("hello")
            res = fut.result(timeout=60)
        finally:
            eng.close()
        self.assertEqual(res.status, AgentStatus.COMPLETED)

    def test_file_roundtrip_desktop_to_tools(self):
        """Desktop→Tools: real action-module functions through the executor,
        gated by the permission engine, on a sandbox dir under home."""
        sandbox = Path.home() / ".shirazi_agent_test"
        sandbox.mkdir(exist_ok=True)
        eng = create_agent(provider=MockProvider("ok"),
                           confirm_hook=lambda t, a, r: True)
        try:
            out1 = eng.execute_tool("create_file",
                                    {"path": str(sandbox), "name": "probe.txt",
                                     "content": "hello agent"})
            self.assertIn("File created", out1)
            out2 = eng.execute_tool("read_file",
                                    {"path": str(sandbox), "name": "probe.txt"})
            self.assertIn("hello agent", out2)
            out3 = eng.execute_tool("delete_file",
                                    {"path": str(sandbox), "name": "probe.txt"})
            self.assertTrue("Moved to Trash" in out3 or "send2trash" in out3)
        finally:
            eng.close()
            shutil.rmtree(sandbox, ignore_errors=True)

    def test_plan_explains_itself_to_user(self):
        eng = create_agent(provider=MockProvider("ok"))
        try:
            intent = detect_intent("Open Chrome and search for today's weather")
            plan = build_plan(intent, available=set(eng.available_tools()))
            # the executor fills permission levels from the permission engine
            for s in plan.steps:
                s.permission = check(s.tool, s.args).level.value
            text = plan.explain()
        finally:
            eng.close()
        self.assertIn("app_open", text)
        self.assertIn("browser_search", text)
        self.assertIn("[USER_CONFIRMATION]", text)  # permission levels shown


# ── 6. Optional-dependency honesty ──────────────────────────────────────────

class TestOptionalDeps(unittest.TestCase):
    def test_browser_tools_report_missing_playwright(self):
        from core.agent import tools_browser as tb
        if tb._playwright() is not None:
            self.skipTest("playwright is installed — honesty path not exercised")
        self.assertIn("pip install playwright",
                      tb.browser_click({"url": "https://example.com",
                                        "text": "x"}))
        self.assertIn("pip install playwright",
                      tb.browser_navigate({"url": "https://example.com"}))
        self.assertIn("pip install playwright",
                      tb.browser_type({"url": "https://example.com",
                                       "selector": "input", "text": "x"}))
        ok, reason = tb.playwright_available()
        self.assertFalse(ok)
        self.assertIn("pip install playwright", reason)

    def test_browser_open_static_fallback_is_labelled(self):
        from core.agent import tools_browser as tb
        if tb._playwright() is not None:
            self.skipTest("playwright is installed")
        orig = tb._static_fetch
        tb._static_fetch = lambda url, timeout=20.0: (_ for _ in ()).throw(
            RuntimeError("no network in test"))
        try:
            out = tb.browser_open({"url": "https://example.com"})
        finally:
            tb._static_fetch = orig
        self.assertIn("pip install playwright", out)

    def test_ocr_reports_missing_tesseract(self):
        if shutil.which("tesseract") is not None:
            self.skipTest("tesseract is installed — honesty path not exercised")
        from core.agent.vision import ScreenData
        v = Vision()
        out = v.ocr(ScreenData(png=b""))
        self.assertFalse(out["available"])
        self.assertIn("tesseract", out["message"].lower())
        status = agent.vision_status()
        self.assertFalse(status["ocr"]["available"])

    def test_processes_honest_without_psutil(self):
        from core.agent import tools_system as ts
        out = ts.processes({})
        self.assertIsInstance(out, str)
        self.assertTrue(out)  # either a table or the install hint


# ── 7. Registry install + declarations + redaction ──────────────────────────

class TestRegistryAndHygiene(unittest.TestCase):
    def test_install_registers_all_with_levels(self):
        reg = build_registry(inline=[])
        n = agent.install_agent_tools(reg)
        self.assertEqual(n, len(agent.register.names()))
        self.assertTrue(reg.has("delete_file"))
        self.assertEqual(reg.get("delete_file").permission,
                         Level.USER_CONFIRMATION)
        self.assertEqual(reg.get("mouse_move").permission, Level.SAFE)
        self.assertEqual(reg.get("browser_search").permission, Level.READ_ONLY)
        self.assertEqual(reg.get("shell_run").permission, Level.USER_CONFIRMATION)
        self.assertEqual(reg.get("research_topic").permission, Level.READ_ONLY)

    def test_declarations_are_valid(self):
        decls = agent.agent_declarations()
        self.assertTrue(decls)
        for d in decls:
            self.assertIn("name", d)
            self.assertIn("description", d)
            self.assertIn("parameters", d)
            self.assertTrue(d["name"])

    def test_no_secret_in_logs(self):
        line = 'agent tool shell_run api_key="sk-test-SECRETVALUE12345"'
        redacted = logger.redact(line)
        self.assertNotIn("SECRETVALUE12345", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_event_topics_are_well_known(self):
        for topic in ("agent.plan", "agent.step", "agent.done"):
            self.assertIn(topic, events.WELL_KNOWN)


if __name__ == "__main__":
    unittest.main()
