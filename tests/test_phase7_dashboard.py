"""Tests for the Phase-7 mobile dashboard: PWA agent API, phone
confirmation flow, /ws/cmd remote control, audio endpoints, quick commands,
and PIN throttling (Phase-2 regression).

Zero network by design: the agent engine is mocked, websockets run through
FastAPI's TestClient, and hardware probes assert the *honest*
unavailability path (sounddevice/pyautogui absent in this sandbox).
"""

import contextlib
import queue
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from dashboard.server import DashboardServer
from core.agent.executor import AgentResult, AgentStatus


# ── Mocks ────────────────────────────────────────────────────────────────

class MockAgent:
    """Agent stand-in. The phone confirm hook is honoured synchronously,
    exactly like the real executor's gated-step path."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.calls: list = []

    def run_sync(self, text: str, *, confirm_hook=None):
        self.calls.append(text)
        if self.mode == "confirm":
            ok = (confirm_hook("file_delete", {"path": "/tmp/x"},
                               "needs approval")
                  if confirm_hook is not None else False)
            if ok:
                return AgentResult(status=AgentStatus.COMPLETED,
                                   answer="file deleted (mock)")
            return AgentResult(status=AgentStatus.CANCELLED,
                               answer="cancelled by you (mock)")
        return AgentResult(status=AgentStatus.COMPLETED,
                           answer="mocked answer")

    def execute_tool(self, name: str, args: dict, *, confirm_hook=None):
        self.calls.append((name, args))
        return f"mock:{name}"


@contextlib.contextmanager
def _logged_in(pin: str = "P7TEST"):
    """Build a server inside ONE shared TestClient portal (context manager
    form) and mint a real bearer token through /login.

    The shared portal keeps a single event loop alive for the whole test —
    exactly like uvicorn in production — so the server's threadsafe
    confirmation broadcasts reach the websocket clients. A bare
    ``TestClient(...)`` without ``with`` spins a fresh, short-lived loop per
    request, which silently breaks cross-thread emits.
    """
    srv = DashboardServer()
    srv._pending_keys[pin] = time.time() + 600
    with TestClient(srv.app) as client:
        r = client.post("/login", json={"pin": pin})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        assert token
        # The login success broadcasts "Remote connection established" on
        # the portal loop; that broadcast captures srv._loop for the
        # confirm flow.
        for _ in range(250):
            if srv._loop is not None and srv._loop.is_running():
                break
            time.sleep(0.02)
        assert srv._loop is not None and srv._loop.is_running(), \
            "server never captured a live event loop"
        yield srv, client, token


def _authed(client, token):
    return {"Authorization": f"Bearer {token}"}


# ── 1. PIN throttling (Phase-2 regression) ───────────────────────────────

class TestPinThrottling(unittest.TestCase):
    def test_lockout_after_five_bad_pins(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            for _ in range(5):
                r = client.post("/login", json={"pin": "WRONG"})
                self.assertEqual(r.status_code, 401)
            # 6th attempt (even with the right key) is locked out.
            srv._pending_keys["RIGHT"] = time.time() + 600
            r = client.post("/login", json={"pin": "RIGHT"})
            self.assertEqual(r.status_code, 429)
            self.assertIn("Too many attempts", r.json()["error"])

    def test_success_resets_counter(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            for _ in range(4):
                client.post("/login", json={"pin": "WRONG"})
            srv._pending_keys["OKKEY"] = time.time() + 600
            r = client.post("/login", json={"pin": "OKKEY"})
            self.assertEqual(r.status_code, 200)
            # Counter cleared: five fresh failures are needed again, not one.
            r = client.post("/login", json={"pin": "WRONG"})
            self.assertEqual(r.status_code, 401)


# ── 2. /api/agent with mocked engine ─────────────────────────────────────

class TestAgentApi(unittest.TestCase):
    def test_unauthenticated(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            r = client.post("/api/agent", json={"text": "hi"})
            self.assertEqual(r.status_code, 401)

    def test_no_agent_engine(self):
        with _logged_in("P7A") as (srv, client, token):
            r = client.post("/api/agent", json={"text": "hi"},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 503)
            self.assertIn("not available", r.json()["error"])

    def test_empty_text_rejected(self):
        with _logged_in("P7B") as (srv, client, token):
            srv.set_agent(MockAgent())
            r = client.post("/api/agent", json={"text": "   "},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 400)

    def test_mocked_agent_completed(self):
        with _logged_in("P7C") as (srv, client, token):
            agent = MockAgent()
            srv.set_agent(agent)
            r = client.post("/api/agent", json={"text": "hello shirazi"},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["status"], "completed")
            self.assertEqual(body["answer"], "mocked answer")
            self.assertIn("hello shirazi", agent.calls)

    def test_text_too_long_rejected(self):
        with _logged_in("P7D") as (srv, client, token):
            srv.set_agent(MockAgent())
            r = client.post("/api/agent", json={"text": "x" * 2001},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 400)


# ── 3. Phone confirmation round-trip (the real concurrent flow) ──────────
#
# /api/agent runs on a worker thread → the executor's confirm hook calls
# request_phone_confirmation → "confirm_request" is broadcast to the feed
# /ws socket → the phone answers on the /ws/cmd socket → the parked thread
# resumes and the HTTP response completes.

class TestConfirmFlow(unittest.TestCase):
    def _run_confirm(self, approved: bool):
        with _logged_in("P7E" if approved else "P7F") as (srv, client, token):
            srv.set_agent(MockAgent(mode="confirm"))

            outcome = {}

            def do_agent():
                outcome["resp"] = client.post(
                    "/api/agent", json={"text": "delete the temp file"},
                    headers=_authed(client, token))

            with client.websocket_connect(f"/ws?token={token}") as feed:
                # Drain thread for feed messages (receive blocks otherwise).
                msgs: "queue.Queue" = queue.Queue()

                def reader():
                    try:
                        while True:
                            msgs.put(feed.receive_json())
                    except Exception as e:  # socket closed
                        msgs.put(e)

                threading.Thread(target=reader, daemon=True).start()
                t = threading.Thread(target=do_agent)
                t.start()

                # 3a. The agent thread parks a confirmation.
                cid = None
                deadline = time.time() + 15
                while time.time() < deadline:
                    if srv._pending_confirms:
                        cid = next(iter(srv._pending_confirms))
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(cid, "agent never parked a confirmation")

                # 3b. The confirm_request really arrived over the feed socket.
                confirm = None
                deadline = time.time() + 15
                while time.time() < deadline:
                    try:
                        m = msgs.get(timeout=1)
                    except queue.Empty:
                        continue
                    if (isinstance(m, dict)
                            and m.get("type") == "confirm_request"
                            and m.get("id") == cid):
                        confirm = m
                        break
                self.assertIsNotNone(
                    confirm, "confirm_request never arrived on /ws")
                self.assertEqual(confirm["tool"], "file_delete")
                self.assertIn("timeout_s", confirm)

                # 3c. The phone answers on the command channel.
                with client.websocket_connect(
                        f"/ws/cmd?token={token}") as cmd:
                    hello = cmd.receive_json()
                    self.assertEqual(hello["type"], "hello")
                    cmd.send_json({"type": "confirm_response",
                                   "id": cid, "approved": approved})
                    ack = cmd.receive_json()
                    self.assertEqual(ack["type"], "confirm_ack")
                    self.assertEqual(ack["id"], cid)
                    self.assertTrue(ack["ok"])

                # 3d. The parked agent thread resumes and the HTTP call
                # completes.
                t.join(timeout=20)
                self.assertFalse(
                    t.is_alive(),
                    "agent thread did not resume after the decision")
            return outcome["resp"].json()

    def test_approve_resumes_execution(self):
        body = self._run_confirm(approved=True)
        self.assertEqual(body["status"], "completed")
        self.assertIn("file deleted", body["answer"])
        self.assertIsNone(body["pending"])

    def test_deny_cancels_execution(self):
        body = self._run_confirm(approved=False)
        self.assertEqual(body["status"], "cancelled")
        self.assertIn("cancelled", body["answer"])

    def test_unknown_confirm_id_rejected(self):
        with _logged_in("P7G") as (srv, client, token):
            srv.set_agent(MockAgent())
            r = client.post("/api/agent/confirm",
                            json={"id": "nope", "approved": True},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 404)


# ── 4. /ws/cmd remote control ────────────────────────────────────────────

class TestCmdSocket(unittest.TestCase):
    def test_bad_token_rejected(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            with self.assertRaises(Exception):
                with client.websocket_connect("/ws/cmd?token=bogus"):
                    pass

    def test_ping_pong_and_dpad_allowlist(self):
        with _logged_in("P7H") as (srv, client, token):
            srv.set_agent(MockAgent())
            with client.websocket_connect(f"/ws/cmd?token={token}") as cmd:
                hello = cmd.receive_json()
                self.assertEqual(hello["server"], "shirazi")
                cmd.send_json({"type": "ping"})
                self.assertEqual(cmd.receive_json()["type"], "pong")

                # Allowlisted key → tool dispatched (mock returns ok).
                cmd.send_json({"type": "dpad", "key": "enter"})
                m = cmd.receive_json()
                self.assertEqual(m["type"], "key")
                self.assertTrue(m["ok"], m)

                # Not on the allowlist → rejected, never dispatched.
                cmd.send_json({"type": "dpad", "key": "rm -rf"})
                m = cmd.receive_json()
                self.assertEqual(m["type"], "error")
                self.assertIn("unknown key", m["error"])

    def test_touchpad_click_validation(self):
        with _logged_in("P7I2") as (srv, client, token):
            srv.set_agent(MockAgent())
            with client.websocket_connect(f"/ws/cmd?token={token}") as cmd:
                cmd.receive_json()  # hello
                cmd.send_json({"type": "touchpad_click", "button": "left"})
                m = cmd.receive_json()
                self.assertEqual(m["type"], "click")
                self.assertTrue(m["ok"], m)

                cmd.send_json({"type": "touchpad_click", "button": "middle"})
                m = cmd.receive_json()
                self.assertEqual(m["type"], "error")

    def test_unknown_message_rejected(self):
        with _logged_in("P7J2") as (srv, client, token):
            srv.set_agent(MockAgent())
            with client.websocket_connect(f"/ws/cmd?token={token}") as cmd:
                cmd.receive_json()  # hello
                cmd.send_json({"type": "launch_missiles"})
                m = cmd.receive_json()
                self.assertEqual(m["type"], "error")

    def test_api_command_rate_limit(self):
        # 120 msgs / 60 s per token on /api/command; the 121st is dropped.
        with _logged_in("P7K2") as (srv, client, token):
            last = None
            for _ in range(125):
                last = client.post("/api/command", json={"text": "x"},
                                   headers=_authed(client, token))
            self.assertEqual(last.status_code, 429)

    def test_api_command_validation(self):
        with _logged_in("P7L2") as (srv, client, token):
            h = _authed(client, token)
            r = client.post("/api/command", json={"text": "x" * 1001},
                            headers=h)
            self.assertEqual(r.status_code, 400)
            # Unauthenticated stays 401 regardless.
            r = client.post("/api/command", json={"text": "x"})
            self.assertEqual(r.status_code, 401)


# ── 5. Audio endpoints ───────────────────────────────────────────────────

class TestAudioEndpoints(unittest.TestCase):
    def test_devices_degraded_without_sounddevice(self):
        with _logged_in("P7M2") as (srv, client, token):
            r = client.get("/api/audio-devices",
                           headers=_authed(client, token))
            self.assertEqual(r.status_code, 200)
            body = r.json()
            # In this sandbox sounddevice is absent: honest unavailability,
            # never a 500.
            self.assertIn("available", body)
            if not body["available"]:
                self.assertIn("error", body)

    def test_select_invalid_kind(self):
        with _logged_in("P7N2") as (srv, client, token):
            r = client.post("/api/audio-devices",
                            json={"kind": "subwoofer", "name": "x"},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 400)

    def test_unauthenticated(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            r = client.get("/api/audio-devices")
            self.assertEqual(r.status_code, 401)


# ── 6. Quick commands ────────────────────────────────────────────────────

class TestQuickCommands(unittest.TestCase):
    def test_defaults_present(self):
        with _logged_in("P7O") as (srv, client, token):
            r = client.get("/api/quick-commands",
                           headers=_authed(client, token))
            self.assertEqual(r.status_code, 200)
            cmds = r.json()["commands"]
            self.assertEqual(len(cmds), 8)
            self.assertTrue(all(c["id"] and c["label"] and c["prompt"]
                                for c in cmds))

    def test_run_unknown_id(self):
        with _logged_in("P7P") as (srv, client, token):
            r = client.post("/api/quick-commands/run", json={"id": "nope"},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 404)

    def test_run_routes_through_agent(self):
        with _logged_in("P7Q") as (srv, client, token):
            agent = MockAgent()
            srv.set_agent(agent)
            cmds = client.get(
                "/api/quick-commands",
                headers=_authed(client, token)).json()["commands"]
            r = client.post("/api/quick-commands/run",
                            json={"id": cmds[0]["id"]},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["status"], "completed")
            self.assertEqual(body["answer"], "mocked answer")
            self.assertEqual(agent.calls[-1], cmds[0]["prompt"])

    def test_run_without_agent_is_503(self):
        with _logged_in("P7R") as (srv, client, token):
            cmds = client.get(
                "/api/quick-commands",
                headers=_authed(client, token)).json()["commands"]
            r = client.post("/api/quick-commands/run",
                            json={"id": cmds[0]["id"]},
                            headers=_authed(client, token))
            self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main(verbosity=2)
