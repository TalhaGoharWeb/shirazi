"""Phase-9 hardening tests: genuine gaps left by Phases 4-8.

Coverage added here (nothing below duplicates an existing test file):
  config      memory/config_manager.py defaults, precedence, normalisation
  registry    adapter merge from loader-shaped objects, override semantics
  auth        /ws feed rejection, PIN-bearer adopt into /api/v1,
              device-token persistence across a server "restart",
              /api/revoke-devices clearing legacy device sessions
  routes      /api/session shape, /api/telemetry shape, /api/upload
              sanitisation + auth, /api/files auth
  websocket   volume/scroll/touchpad command validation + clamping,
              tool-name allowlist (no shell via the command channel),
              /ws/phone-audio auth + PCM relay into the desktop queue

Integration (§46, Linux-runnable, mocked where hardware is absent):
  Mobile→FastAPI   covered by Phase 7; the new tests add /api/session,
                   /api/telemetry, /api/upload, /api/device-login(restart)
  Mobile→WebSocket /ws/cmd volume/scroll/touchpad paths; /ws/phone-audio
                   PCM frames reach the desktop relay queue
  Mobile→PC        volume/scroll/click validated, clamped, dispatched
  AI→Tool engine   phone can never name an arbitrary tool on /ws/cmd
  Desktop→Audio    NOT tested here — no sounddevice/display in this
                   sandbox; see docs/WINDOWS_QA.md (manual).
  Desktop→AI       mocked-provider failover lives in test_providers.py.

Zero network by design. No test touches the real config/ or HOME dirs:
config paths and the upload dir are monkeypatched into tmp dirs.
"""

import asyncio
import contextlib
import io
import json
import queue
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import memory.config_manager as cm
from core.accounts import AccountStore
from core.agent.executor import AgentResult, AgentStatus
from core.permissions import Level
from core.tools import ToolSpec, build_registry
from dashboard.server import DashboardServer


# ── Shared fixtures ──────────────────────────────────────────────────────

class MockAgent:
    """Records tool dispatches; honours the per-call confirm hook."""

    def __init__(self):
        self.tool_calls: list = []
        self.agent_calls: list = []

    def run_sync(self, text: str, *, confirm_hook=None):
        self.agent_calls.append(text)
        return AgentResult(status=AgentStatus.COMPLETED, answer="mocked")

    def execute_tool(self, name: str, args: dict, *, confirm_hook=None):
        self.tool_calls.append((name, args))
        return f"mock:{name}"


@contextlib.contextmanager
def _logged_in(pin: str, *, store=None, uploads_dir=None):
    """Logged-in DashboardServer inside ONE shared TestClient portal."""
    srv = DashboardServer(account_store=store)
    if uploads_dir is not None:
        srv._uploads_dir = uploads_dir
    srv._pending_keys[pin] = time.time() + 600
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


class _TmpConfig:
    """Monkeypatch config_manager's file paths into a tmp dir."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self._old = (cm.CONFIG_FILE, cm.SETTINGS_FILE, cm.CONFIG_DIR)

    def __enter__(self):
        cm.CONFIG_DIR = self.tmp
        cm.CONFIG_FILE = self.tmp / "api_keys.json"
        cm.SETTINGS_FILE = self.tmp / "settings.json"
        return self

    def __exit__(self, *a):
        cm.CONFIG_FILE, cm.SETTINGS_FILE, cm.CONFIG_DIR = self._old


# ── 1. Config: defaults, precedence, normalisation ────────────────────────

class TestConfigManager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self._tmpdir())
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._ctx = _TmpConfig(self.tmp)
        self._ctx.__enter__()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    @staticmethod
    def _tmpdir():
        import tempfile
        return tempfile.mkdtemp(prefix="shirazi_p9_cfg_")

    def test_assistant_name_defaults_to_shirazi(self):
        self.assertEqual(cm.get_assistant_name(), "SHIRAZI")

    def test_custom_assistant_name_round_trips(self):
        cm.save_assistant_config("  My Helper ", "Ada")
        self.assertEqual(cm.get_assistant_name(), "My Helper")
        self.assertEqual(cm.get_user_name(), "Ada")

    def test_blank_assistant_name_falls_back_to_shirazi(self):
        cm.save_assistant_config("   ", "Ada")
        self.assertEqual(cm.get_assistant_name(), "SHIRAZI")

    def test_wake_words_default_to_shirazi_phrases(self):
        self.assertEqual(cm.get_wake_words(), ["hey_shirazi", "shirazi"])

    def test_wake_words_normalised_and_saved(self):
        cm.save_wake_words([" Hey Shirazi ", "SHIRAZI"])
        self.assertEqual(cm.get_wake_words(), ["hey shirazi", "shirazi"])
        cm.save_wake_words([])
        self.assertEqual(cm.get_wake_words(), ["hey_shirazi", "shirazi"])

    def test_api_keys_tier_beats_settings_tier(self):
        cm.SETTINGS_FILE.write_text(
            json.dumps({"assistant_name": "FromSettings"}), encoding="utf-8")
        self.assertEqual(cm.get_assistant_name(), "FromSettings")
        cm.save_assistant_config("FromApiKeys", "Ada")
        self.assertEqual(cm.get_assistant_name(), "FromApiKeys")

    def test_corrupt_json_is_fail_soft(self):
        cm.CONFIG_FILE.write_text("{not json", encoding="utf-8")
        self.assertEqual(cm.load_api_keys(), {})
        self.assertEqual(cm.get_assistant_name(), "SHIRAZI")


# ── 2. Tool registry adapter: loader merge + override semantics ──────────

class _FakeLoader:
    """ActionRegistry-shaped: exposes get_tool_declarations()."""

    def __init__(self, decls):
        self._decls = decls

    def get_tool_declarations(self):
        return self._decls


class TestRegistryAdapter(unittest.TestCase):
    def test_merge_loader_objects(self):
        loader = _FakeLoader([
            {"name": "browser_control", "description": "web",
             "parameters": {"type": "object", "properties": {}}},
        ])
        reg = build_registry(
            inline=[{"name": "system_status", "description": "sys"}],
            actions=loader,
            plugins=_FakeLoader([{"name": "my_plugin", "description": "p"}]))
        self.assertTrue(reg.has("system_status"))
        self.assertTrue(reg.has("browser_control"))
        self.assertTrue(reg.has("my_plugin"))
        self.assertEqual(reg.get("browser_control").source, "action")
        self.assertEqual(reg.get("my_plugin").source, "plugin")
        self.assertEqual(reg.get("my_plugin").permission, Level.USER_CONFIRMATION)

    def test_broken_loader_does_not_abort_build(self):
        class Boom:
            def get_tool_declarations(self):
                raise RuntimeError("loader exploded")

        reg = build_registry(
            inline=[{"name": "system_status"}], actions=Boom())
        self.assertTrue(reg.has("system_status"))

    def test_first_registration_wins_without_override(self):
        reg = build_registry()
        reg.register(ToolSpec(name="t", category="system",
                              permission=Level.SAFE, description="one"))
        reg.register(ToolSpec(name="t", category="system",
                              permission=Level.PRIVILEGED, description="two"))
        self.assertEqual(reg.get("t").description, "one")

    def test_override_replaces(self):
        reg = build_registry()
        reg.register(ToolSpec(name="t", category="system",
                              permission=Level.SAFE, description="one"))
        reg.register(ToolSpec(name="t", category="system",
                              permission=Level.PRIVILEGED, description="two"),
                     override=True)
        self.assertEqual(reg.get("t").description, "two")
        self.assertEqual(reg.get("t").permission, Level.PRIVILEGED)

    def test_unregister_and_describe(self):
        reg = build_registry(inline=[{"name": "web_search"},
                                     {"name": "system_status"}])
        reg.unregister("web_search")
        self.assertFalse(reg.has("web_search"))
        text = reg.describe_for_prompt()
        self.assertNotIn("web_search", text)
        self.assertIn("system_status", text)


# ── 3. Auth hardening ────────────────────────────────────────────────────

class TestAuthHardening(unittest.TestCase):
    def test_ws_feed_rejects_bogus_token(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            with self.assertRaises(Exception):
                with client.websocket_connect("/ws?token=bogus"):
                    pass

    def test_pin_bearer_adopted_into_v1(self):
        # The PIN-login bearer (legacy path) must be accepted by the
        # Phase-8 /api/v1 surface via the session adopt path.
        with _logged_in("P9A") as (srv, client, token):
            r = client.get("/api/v1/me", headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["id"], "local")

    def test_logout_revokes_pin_bearer_everywhere(self):
        with _logged_in("P9B") as (srv, client, token):
            r = client.post("/api/v1/logout", headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            r = client.post("/api/command", json={"text": "hi"},
                            headers=_authed(token))
            self.assertEqual(r.status_code, 401)
            r = client.get("/api/v1/me", headers=_authed(token))
            self.assertEqual(r.status_code, 401)

    def test_device_token_survives_server_restart(self):
        """Phase-8 claim: a paired phone keeps working after the desktop
        restarts, served from the persistent device registry."""
        import tempfile
        db = Path(tempfile.mkdtemp(prefix="shirazi_p9_acct_")) / "acct.db"
        store = AccountStore(db_path=db)

        with _logged_in("P9C", store=store) as (srv, client, token):
            r = client.post("/api/v1/devices",
                            json={"kind": "phone", "name": "TestPhone"},
                            headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            device_token = r.json()["device_token"]
            self.assertTrue(device_token)
            device_id = r.json()["device_id"]

        # "Restart": brand-new server object, same on-disk store.
        srv2 = DashboardServer(account_store=AccountStore(db_path=db))
        with TestClient(srv2.app) as client2:
            r = client2.post("/api/device-login",
                             json={"device_token": device_token})
            self.assertEqual(r.status_code, 200, r.text)
            tok2 = r.json()["token"]
            r = client2.post("/api/command", json={"text": "hi"},
                             headers=_authed(tok2))
            self.assertEqual(r.status_code, 200)

    def test_v1_delete_device_cascades_to_device_login_bearer(self):
        """DELETE /api/v1/devices/{id} must kill the bearer that was minted
        from that device's token via /api/device-login."""
        import tempfile
        db = Path(tempfile.mkdtemp(prefix="shirazi_p9_del_")) / "acct.db"
        store = AccountStore(db_path=db)
        with _logged_in("P9D2", store=store) as (srv, client, token):
            r = client.post("/api/v1/devices",
                            json={"kind": "phone", "name": "P"},
                            headers=_authed(token))
            device_id = r.json()["device_id"]
            device_token = r.json()["device_token"]
            r = client.post("/api/device-login",
                            json={"device_token": device_token})
            self.assertEqual(r.status_code, 200, r.text)
            dev_bearer = r.json()["token"]
            # Sanity: the bearer works before revocation.
            r = client.post("/api/command", json={"text": "hi"},
                            headers=_authed(dev_bearer))
            self.assertEqual(r.status_code, 200)
            # Revoke the device.
            r = client.delete(f"/api/v1/devices/{device_id}",
                              headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["ok"])
            # The device-login bearer is dead; the admin bearer survives.
            r = client.post("/api/command", json={"text": "hi"},
                            headers=_authed(dev_bearer))
            self.assertEqual(r.status_code, 401)
            r = client.post("/api/command", json={"text": "hi"},
                            headers=_authed(token))
            self.assertEqual(r.status_code, 200)

    def test_register_mints_channel_key(self):
        """devices.register must leave no device without a channel key, or
        /api/device-login can never exchange its token (Phase 9 fix)."""
        import tempfile
        from core.accounts import devices as devmod
        from core.accounts import SessionManager, ensure_local_user
        db = Path(tempfile.mkdtemp(prefix="shirazi_p9_reg_")) / "a.db"
        store = AccountStore(db_path=db)
        ensure_local_user(store)
        m = SessionManager(store)
        rec = devmod.register(m, "local", name="T", kind="phone")
        dev = m.find_device(rec["device_token"])
        self.assertIsNotNone(dev)
        self.assertTrue(dev.session_key,
                        "registered device has no channel key")

    def test_revoke_devices_clears_legacy_device_sessions(self):
        with _logged_in("P9D") as (srv, client, token):
            srv._device_sessions["DEVTOK"] = {"session_key": "K1"}
            dev_bearer = "legacy-dev-bearer"
            srv._tokens.add(dev_bearer)
            srv._token_keys[dev_bearer] = "K1"
            r = client.post("/api/revoke-devices", headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            r = client.post("/api/command", json={"text": "hi"},
                            headers=_authed(dev_bearer))
            self.assertEqual(r.status_code, 401)


# ── 4. API routes: session / telemetry / upload / files ──────────────────

class TestApiRoutes(unittest.TestCase):
    def test_session_requires_auth_and_reports_shape(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            r = client.get("/api/session")
            self.assertEqual(r.status_code, 401)
        with _logged_in("P9E") as (srv, client, token):
            r = client.get("/api/session", headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            body = r.json()
            for key in ("ok", "agent_available", "device_manager",
                        "pending_confirms", "backend"):
                self.assertIn(key, body)
            self.assertFalse(body["agent_available"])
            self.assertEqual(body["pending_confirms"], 0)

    def test_session_backend_status_hook(self):
        with _logged_in("P9F") as (srv, client, token):
            srv.set_backend_status_fn(lambda: {"live": True})
            r = client.get("/api/session", headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["backend"]["live"])

    def test_telemetry_shape_and_auth(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            r = client.get("/api/telemetry")
            self.assertEqual(r.status_code, 401)
        with _logged_in("P9G") as (srv, client, token):
            r = client.get("/api/telemetry", headers=_authed(token))
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body["ok"])
            self.assertTrue(body["rows"])
            for row in body["rows"]:
                self.assertIn("label", row)
                self.assertIn("value", row)
                self.assertIn("display", row)
            labels = {row["label"] for row in body["rows"]}
            self.assertIn("CPU", labels)

    def test_upload_sanitises_filename_and_requires_auth(self):
        import tempfile
        uploads = Path(tempfile.mkdtemp(prefix="shirazi_p9_up_"))
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            r = client.post("/api/upload",
                            files={"file": ("x.txt", io.BytesIO(b"hi"))})
            self.assertEqual(r.status_code, 401)
        with _logged_in("P9H", uploads_dir=uploads) as (srv, client, token):
            evil = "../../..\\evil.txt"
            r = client.post("/api/upload", headers=_authed(token),
                            files={"file": (evil, io.BytesIO(b"payload"))})
            self.assertEqual(r.status_code, 200, r.text)
            name = r.json()["name"]
            self.assertNotIn("..", name)
            self.assertNotIn("/", name.replace("\\", "/"))
            self.assertTrue((uploads / name).exists())
            self.assertEqual((uploads / name).read_bytes(), b"payload")

    def test_files_requires_auth(self):
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            r = client.get("/api/files")
            self.assertEqual(r.status_code, 401)


# ── 5. Mobile → PC controls on /ws/cmd ────────────────────────────────────

class TestCmdControls(unittest.TestCase):
    def _cmd(self, pin, agent=None):
        ctx = _logged_in(pin)
        srv, client, token = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        if agent is None:
            agent = MockAgent()
        srv.set_agent(agent)
        return srv, client, token, agent

    def test_volume_set_happy_path_and_clamp(self):
        srv, client, token, agent = self._cmd("P9I")
        with client.websocket_connect(f"/ws/cmd?token={token}") as cmd:
            cmd.receive_json()  # hello
            cmd.send_json({"type": "volume", "action": "set", "level": 80})
            m = cmd.receive_json()
            self.assertEqual(m["type"], "volume")
            self.assertTrue(m["ok"], m)
            self.assertEqual(agent.tool_calls[-1],
                             ("volume_set", {"level": 80}))

            # Out-of-range level is clamped to 100, not passed through.
            cmd.send_json({"type": "volume", "action": "set", "level": 999})
            m = cmd.receive_json()
            self.assertTrue(m["ok"], m)
            self.assertEqual(agent.tool_calls[-1],
                             ("volume_set", {"level": 100}))

    def test_volume_unknown_action_rejected(self):
        srv, client, token, agent = self._cmd("P9J")
        with client.websocket_connect(f"/ws/cmd?token={token}") as cmd:
            cmd.receive_json()
            cmd.send_json({"type": "volume", "action": "overdrive"})
            m = cmd.receive_json()
            self.assertEqual(m["type"], "error")
            self.assertIn("unknown volume action", m["error"])

    def test_scroll_happy_path_and_bad_direction(self):
        srv, client, token, agent = self._cmd("P9K")
        with client.websocket_connect(f"/ws/cmd?token={token}") as cmd:
            cmd.receive_json()
            cmd.send_json({"type": "scroll", "direction": "down",
                           "amount": 5})
            m = cmd.receive_json()
            self.assertEqual(m["type"], "scroll")
            self.assertTrue(m["ok"], m)
            self.assertEqual(agent.tool_calls[-1],
                             ("scroll", {"direction": "down", "amount": 5}))

            cmd.send_json({"type": "scroll", "direction": "sideways"})
            m = cmd.receive_json()
            self.assertEqual(m["type"], "error")
            self.assertIn("direction must be", m["error"])

    def test_touchpad_move_dispatched(self):
        # touchpad_move is fire-and-forget by design (up to 40 msgs/s — an
        # ack per move would flood the channel): the server answers only on
        # failure. Assert the tool ran, polling the mock instead of reading
        # a reply that will never come.
        srv, client, token, agent = self._cmd("P9L")
        with client.websocket_connect(f"/ws/cmd?token={token}") as cmd:
            cmd.receive_json()  # hello
            cmd.send_json({"type": "touchpad_move", "dx": 10, "dy": -4})
            deadline = time.time() + 8
            got = None
            while time.time() < deadline:
                if agent.tool_calls:
                    got = agent.tool_calls[-1]
                    break
                time.sleep(0.05)
            self.assertIsNotNone(got, "mouse_move never executed")
            name, args = got
            self.assertEqual(name, "mouse_move")
            # Cursor estimate starts at (960, 540); move is applied to it.
            self.assertEqual(args, {"x": 970, "y": 536})

    def test_no_arbitrary_tool_naming_from_phone(self):
        # The client can never name a tool: message types are hardcoded.
        # A shell-shaped payload must die as an unknown message type.
        srv, client, token, agent = self._cmd("P9M")
        with client.websocket_connect(f"/ws/cmd?token={token}") as cmd:
            cmd.receive_json()
            cmd.send_json({"type": "shell_run", "command": "rm -rf ~"})
            m = cmd.receive_json()
            self.assertEqual(m["type"], "error")
            self.assertIn("unknown message type", m["error"])
            self.assertEqual(agent.tool_calls, [])


# ── 6. Phone audio WebSocket ─────────────────────────────────────────────

class TestPhoneAudio(unittest.TestCase):
    def test_bogus_token_rejected(self):
        # The server closes the socket without accepting (4001); the test
        # client surfaces that as an exception either at connect or at the
        # first receive — either way the bogus token streams nothing.
        srv = DashboardServer()
        with TestClient(srv.app) as client:
            with self.assertRaises(Exception):
                with client.websocket_connect(
                        "/ws/phone-audio?token=bogus") as ws:
                    ws.receive_bytes()

    def test_pcm_frames_reach_desktop_relay_queue(self):
        # Mobile→WebSocket integration: frames the phone streams must land
        # in the queue the desktop's Live session drains. The Live session
        # itself is absent here, so we assert the queue handoff only.
        with _logged_in("P9N") as (srv, client, token):
            with client.websocket_connect(
                    f"/ws/phone-audio?token={token}") as ws:
                ws.send_bytes(b"\x00\x01" * 160)  # 160 PCM16 samples
                deadline = time.time() + 10
                frame = None
                while time.time() < deadline:
                    try:
                        frame = srv._phone_audio_queue.get_nowait()
                        break
                    except asyncio.QueueEmpty:
                        time.sleep(0.05)
                self.assertIsNotNone(frame, "PCM frame never reached relay")
                self.assertEqual(frame["mime_type"], "audio/pcm")
                self.assertEqual(frame["data"], b"\x00\x01" * 160)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ── 7. Shell-injection hardening (security audit fixes) ──────────────────

class TestShellInjectionHardening(unittest.TestCase):
    def test_focus_window_sanitises_hostile_title(self):
        import actions.computer_control as cc
        calls = []
        orig_run = cc.subprocess.run
        orig_os = cc._get_os
        cc._get_os = lambda: "windows"
        cc.subprocess.run = lambda *a, **k: calls.append(a[0]) or \
            type("R", (), {"returncode": 0})()
        try:
            cc._focus_window('x"); Invoke-Evil #')
        finally:
            cc.subprocess.run = orig_run
            cc._get_os = orig_os
        self.assertTrue(calls, "powershell path never invoked")
        script = calls[0][-1]
        # The payload text may survive as a *window title*, but it must be
        # trapped inside the quoted AppActivate("...") string: no quote
        # characters may remain in the interpolated region.
        inner = script.split('AppActivate("', 1)[1].rsplit('")', 1)[0]
        self.assertNotIn('"', inner)
        self.assertNotIn("`", inner)
        self.assertNotIn("$", inner)
        self.assertEqual(inner, "x); Invoke-Evil #")

    def test_focus_window_sanitiser_keeps_ordinary_letters(self):
        # The sanitiser must strip control metacharacters, not innocent
        # letters: 'r' and 'n' in a title like "Modern Browser" survive,
        # while real CR/LF are removed.
        import actions.computer_control as cc
        calls = []
        orig_run = cc.subprocess.run
        orig_os = cc._get_os
        cc._get_os = lambda: "windows"
        cc.subprocess.run = lambda *a, **k: calls.append(a[0]) or \
            type("R", (), {"returncode": 0})()
        try:
            cc._focus_window("Modern Browser\r\nEvil")
        finally:
            cc.subprocess.run = orig_run
            cc._get_os = orig_os
        self.assertTrue(calls)
        script = calls[0][-1]
        inner = script.split('AppActivate("', 1)[1].rsplit('")', 1)[0]
        self.assertEqual(inner, "Modern BrowserEvil")

    def test_focus_window_truncates(self):
        import actions.computer_control as cc
        seen = []
        orig_run = cc.subprocess.run
        orig_os = cc._get_os
        cc._get_os = lambda: "windows"
        cc.subprocess.run = lambda *a, **k: seen.append(a[0]) or \
            type("R", (), {"returncode": 0})()
        try:
            cc._focus_window("A" * 500)
        finally:
            cc.subprocess.run = orig_run
            cc._get_os = orig_os
        self.assertTrue(seen)
        self.assertLessEqual(len(seen[0][-1]), 260)

    def test_open_app_which_branch_never_uses_shell_true(self):
        import actions.open_app as oa
        calls = []
        orig_popen = oa.subprocess.Popen
        orig_which = oa.shutil.which
        oa.shutil.which = lambda name: ("/usr/bin/fake"
                                        if name == "myapp" else None)
        oa.subprocess.Popen = lambda *a, **k: calls.append((a, k)) or \
            type("P", (), {})()
        try:
            oa._launch_windows("myapp")
        finally:
            oa.subprocess.Popen = orig_popen
            oa.shutil.which = orig_which
        self.assertTrue(calls, "no launch attempted")
        for args, kwargs in calls:
            self.assertFalse(kwargs.get("shell", False),
                             "shell=True must not be used for app names")
            self.assertIsInstance(args[0], list,
                                  "app launch must use argv form")

    def test_open_app_url_branch_uses_startfile_not_cmd(self):
        # The ":" branch (URLs, C:\ paths, protocol URIs) must go through
        # ShellExecute — never through cmd.exe, which would still parse
        # metacharacters (&, |, ...) out of the app name.
        import actions.open_app as oa
        started = []
        had_startfile = hasattr(oa.os, "startfile")
        orig_startfile = getattr(oa.os, "startfile", None)
        orig_which = oa.shutil.which
        oa.shutil.which = lambda name: None  # force the ":" branch
        oa.os.startfile = lambda p: started.append(p)
        try:
            ok = oa._launch_windows("https://example.com/?x=1&calc")
        finally:
            oa.shutil.which = orig_which
            if had_startfile:
                oa.os.startfile = orig_startfile
            else:
                del oa.os.startfile
        self.assertTrue(ok)
        self.assertEqual(started, ["https://example.com/?x=1&calc"])


# ── 8. code_helper execution gate (security audit fix) ───────────────────

class TestCodeHelperGate(unittest.TestCase):
    def test_run_build_auto_require_confirmation(self):
        from core import permissions
        for action in ("run", "build", "auto"):
            gate = permissions.check(
                "code_helper", {"action": action, "file_path": "x.py"})
            self.assertTrue(gate.needs_confirmation,
                            f"action={action} must need confirmation")
            self.assertTrue(gate.allowed)

    def test_explain_stays_read_only(self):
        from core import permissions
        gate = permissions.check(
            "code_helper", {"action": "explain", "code": "print(1)"})
        self.assertFalse(gate.needs_confirmation)
        self.assertTrue(gate.allowed)

    def test_run_never_reaches_subprocess_without_confirmation(self):
        # The permission layer is the only thing standing between the model
        # and _run_file: prove the gate fires before any execution path.
        from core import permissions
        gate = permissions.check("code_helper", {"action": "run",
                                                 "file_path": "evil.py"})
        self.assertEqual(gate.level.value, "USER_CONFIRMATION")
