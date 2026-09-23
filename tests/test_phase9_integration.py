"""Phase 9 — end-to-end integration suite (exactly 7 tests).

Each test crosses a real product seam with the far side stubbed or mocked,
so the suite proves the wiring without needing hardware, network, or keys:

1. Desktop → AI brain (provider injected, no network)
2. Desktop → audio device path (sounddevice stubbed, no hardware)
3. Desktop → tool engine (real SAFE tool, real permission gate)
4. Mobile → FastAPI (HTTP status + command queue over TestClient)
5. Mobile → WebSocket (live /ws/cmd round-trip)
6. Mobile → PC controls (scroll + touchpad reach the agent's tools)
7. AI → tool engine (permission gate: SAFE runs, PRIVILEGED denied,
   USER_CONFIRMATION cancelled when the human declines)

Real hardware (mic/speakers) and real providers (Gemini/OpenRouter/Ollama)
stay manual — see docs/WINDOWS_QA.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import ModuleType

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.accounts import AccountStore, ensure_local_user
from dashboard.server import DashboardServer


# ── shared doubles ───────────────────────────────────────────────────────

class StubProvider:
    """Mocked LLM provider: records prompts, never touches the network."""

    def __init__(self, reply: str = "mocked answer"):
        self.reply = reply
        self.calls: list = []

    def generate(self, prompt: str, *, system: str = "",
                 timeout_s: float = 30.0, json_mode: bool = False) -> str:
        self.calls.append(prompt)
        return self.reply


class MockAgent:
    """Records tool dispatches; honours the per-call confirm hook."""

    def __init__(self):
        self.tool_calls: list = []

    def execute_tool(self, name: str, args: dict, *, confirm_hook=None):
        self.tool_calls.append((name, args))
        return f"mock:{name}"


@contextlib.contextmanager
def _logged_in(pin: str, *, store=None, agent=None):
    """Logged-in DashboardServer inside ONE shared TestClient portal."""
    srv = DashboardServer(account_store=store)
    srv._pending_keys[pin] = time.time() + 600
    if agent is not None:
        srv.set_agent(agent)
    with TestClient(srv.app) as client:
        r = client.post("/login", json={"pin": pin})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        assert token
        for _ in range(250):
            if srv._loop is not None and srv._loop.is_running():
                break
            time.sleep(0.02)
        yield srv, client, token


def _authed(token):
    return {"Authorization": f"Bearer {token}"}


# ── 1. Desktop → AI (mocked provider) ────────────────────────────────────

class TestDesktopAI(unittest.TestCase):
    def test_conversation_runs_through_mocked_provider(self):
        from core.agent.executor import Agent, AgentStatus
        provider = StubProvider(reply="Hello! I'm SHIRAZI (mocked).")
        agent = Agent(provider=provider, max_steps=3)
        try:
            result = agent.run_sync("Hello there", timeout=60)
        finally:
            agent.close()
        self.assertTrue(provider.calls, "provider was never consulted")
        self.assertIn("mocked", result.answer)
        self.assertEqual(result.status, AgentStatus.COMPLETED)


# ── 2. Desktop → audio (mocked backend) ──────────────────────────────────

class TestDesktopAudio(unittest.TestCase):
    def test_device_enumeration_and_resolve_with_stubbed_backend(self):
        import core.audio_devices as ad
        orig_query = ad._query
        orig_cache = ad._cache
        orig_usable = ad._usable
        orig_sd = sys.modules.get("sounddevice")

        class FakeSD(ModuleType):
            def query_devices(self):
                return [
                    {"name": "Mock Mic", "max_input_channels": 2,
                     "max_output_channels": 0, "hostapi": 0},
                    {"name": "Mock Speakers", "max_input_channels": 0,
                     "max_output_channels": 2, "hostapi": 0},
                ]

            def query_hostapis(self):
                return [{"name": "FakeAPI"}]

        try:
            ad._query = lambda: {"input": ["Mock Mic"],
                                 "output": ["Mock Speakers"]}
            ad._cache = None
            # _usable() would try to open the device for real — stub the
            # transport probe; this test covers name→index mapping only.
            ad._usable = lambda idx, kind: True
            sys.modules["sounddevice"] = FakeSD("sounddevice")
            self.assertEqual(ad.list_devices("input"), ["Mock Mic"])
            self.assertEqual(ad.list_devices("output"), ["Mock Speakers"])
            # A saved name resolves to a backend index…
            self.assertEqual(ad.resolve("Mock Mic", "input"), 0)
            # …while a missing device degrades to system default, not a crash.
            self.assertIsNone(ad.resolve("Unplugged Headset", "input"))
            self.assertIsNone(ad.resolve("", "output"))
        finally:
            ad._query = orig_query
            ad._cache = orig_cache
            ad._usable = orig_usable
            if orig_sd is None:
                sys.modules.pop("sounddevice", None)
            else:
                sys.modules["sounddevice"] = orig_sd


# ── 3. Desktop → tools (real engine, real gate) ──────────────────────────

class TestDesktopTools(unittest.TestCase):
    def test_safe_tool_executes_through_permission_gate(self):
        from core.agent.executor import Agent
        agent = Agent(provider=StubProvider(), max_steps=1)
        try:
            out = agent.execute_tool("system_stats", {})
        finally:
            agent.close()
        self.assertNotIn("Denied", out)
        self.assertTrue(str(out).strip(), "system_stats returned nothing")


# ── 4. Mobile → FastAPI ──────────────────────────────────────────────────

class TestMobileFastAPI(unittest.TestCase):
    def test_status_and_command_queue(self):
        db = Path(tempfile.mkdtemp(prefix="shirazi_i4_")) / "acct.db"
        store = AccountStore(db_path=db)
        with _logged_in("I400", store=store) as (srv, client, token):
            r = client.get("/api/telemetry", headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body.get("ok"))
            self.assertIn("rows", body)

            r = client.get("/api/telemetry")
            self.assertEqual(r.status_code, 401)

            r = client.post("/api/command", json={"text": "test-cmd-1"},
                            headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json().get("ok"))
            queued = srv._command_queue.get_nowait()
            self.assertEqual(queued, "test-cmd-1")

            r = client.post("/api/command", json={"text": "nope"})
            self.assertEqual(r.status_code, 401)


# ── 5. Mobile → WebSocket ────────────────────────────────────────────────

class TestMobileWebSocket(unittest.TestCase):
    def test_cmd_channel_round_trip(self):
        agent = MockAgent()
        with _logged_in("I500", agent=agent) as (srv, client, token):
            with client.websocket_connect(
                    f"/ws/cmd?token={token}") as ws:
                ws.receive_json()  # hello
                ws.send_json({"type": "volume", "action": "set", "level": 42})
                m = ws.receive_json()
                self.assertEqual(m["type"], "volume")
                self.assertTrue(m["ok"], m)
                self.assertEqual(agent.tool_calls[-1],
                                 ("volume_set", {"level": 42}))


# ── 6. Mobile → PC controls ──────────────────────────────────────────────

class TestMobilePCControls(unittest.TestCase):
    def test_scroll_and_touchpad_reach_agent_tools(self):
        agent = MockAgent()
        with _logged_in("I600", agent=agent) as (srv, client, token):
            with client.websocket_connect(
                    f"/ws/cmd?token={token}") as ws:
                ws.receive_json()  # hello
                ws.send_json({"type": "scroll", "direction": "up",
                              "amount": 3})
                m = ws.receive_json()
                self.assertEqual(m["type"], "scroll")
                self.assertTrue(m["ok"], m)
                self.assertEqual(agent.tool_calls[-1],
                                 ("scroll", {"direction": "up", "amount": 3}))

                # touchpad_move is fire-and-forget (no ack); poll the tool.
                ws.send_json({"type": "touchpad_move", "dx": -7, "dy": 9})
                deadline = time.time() + 8
                got = None
                while time.time() < deadline:
                    if agent.tool_calls and \
                            agent.tool_calls[-1][0] == "mouse_move":
                        got = agent.tool_calls[-1]
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(got, "mouse_move never executed")
                name, args = got
                # Cursor estimate starts at (960, 540); move is applied to it.
                self.assertEqual(args, {"x": 953, "y": 549})


# ── 7. AI → tool engine (permission gate) ───────────────────────────────

class TestAIToolEngine(unittest.TestCase):
    def test_permission_gate_end_to_end(self):
        from core import permissions
        from core.agent.executor import Agent
        agent = Agent(provider=StubProvider(), max_steps=1)
        try:
            # SAFE tool: runs through the real engine.
            out = agent.execute_tool("system_stats", {})
            self.assertNotIn("Denied", out)

            # Unknown tool: fail-closed, never dispatched.
            out = agent.execute_tool("no_such_tool_xyz", {})
            self.assertIn("Unknown tool", out)

            # USER_CONFIRMATION: the human declines → cancelled, never run.
            out = agent.execute_tool("app_open", {"app": "calc"},
                                     confirm_hook=lambda *a: False)
            self.assertIn("Cancelled", out)

            # PRIVILEGED machinery: a tool classified PRIVILEGED is denied
            # while developer mode is off — even with an approving hook.
            # (No shipped tool is classified PRIVILEGED today; this probes
            # the gate itself, then restores the table.)
            table = permissions._DEFAULT_LEVELS
            table["__p9_privileged_probe__"] = permissions.Level.PRIVILEGED
            try:
                out = agent.execute_tool("__p9_privileged_probe__", {},
                                         confirm_hook=lambda *a: True)
            finally:
                del table["__p9_privileged_probe__"]
            self.assertIn("Denied", out)
            self.assertIn("developer mode is off", out)
        finally:
            agent.close()


if __name__ == "__main__":
    unittest.main()
