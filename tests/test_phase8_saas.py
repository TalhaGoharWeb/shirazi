"""Tests for Phase 8 (SaaS foundation): user accounts, sessions, device
registration, per-user provider management (FREE-FIRST), per-user
permissions, usage tracking, the encrypted secret store (migration
idempotency, no plaintext residue), and the dashboard wiring
(bearer auth as the single-user instance of the general mechanism,
/api/v1 routes).

Zero network by design. Dashboard tests need fastapi (skipped without it);
encrypted-file backend tests need `cryptography` (skipped without it).
"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.accounts import AccountStore, SessionManager, ensure_local_user
from core.accounts import secrets as secrets_mod
from core.accounts.models import Role
import core.accounts.providers as prov
import core.accounts.permissions as uperm
from core.accounts.usage import UsageTracker, install_registry_recorder

try:
    from fastapi.testclient import TestClient
    from dashboard.server import DashboardServer
    _FASTAPI = True
except Exception:
    _FASTAPI = False

try:
    import cryptography  # noqa: F401
    _CRYPTO = True
except Exception:
    _CRYPTO = False


def _tmpdir():
    # Honor TMPDIR (this sandbox's /tmp is a tiny tmpfs; the runner sets
    # TMPDIR to a workspace scratch dir).
    d = tempfile.mkdtemp(prefix="shirazi_p8_",
                         dir=os.environ.get("TMPDIR") or None)
    return Path(d)


def _store(path=None):
    d = path or _tmpdir()
    (d / "cfg").mkdir(exist_ok=True)
    return AccountStore(db_path=d / "cfg" / "acct.db")


# ── User store ─────────────────────────────────────────────────────────────

class TestUserStore(unittest.TestCase):
    def setUp(self):
        self.s = _store()

    def test_local_user_idempotent_admin(self):
        uid = ensure_local_user(self.s)
        self.assertEqual(uid, "local")
        u = self.s.get_user("local")
        self.assertTrue(u.is_admin())
        uid2 = ensure_local_user(self.s)
        self.assertEqual(uid2, "local")
        self.assertEqual(len(self.s.list_users()), 1)

    def test_crud(self):
        u = self.s.ensure_user("u1", name="Ada", role=Role.STANDARD,
                               language="ur")
        self.assertEqual(u.name, "Ada")
        self.assertEqual(u.language, "ur")
        self.assertFalse(u.is_admin())
        self.s.update_user("u1", voice="Puck", paid_opt_in=True)
        u2 = self.s.get_user("u1")
        self.assertEqual(u2.voice, "Puck")
        self.assertTrue(u2.paid_opt_in)
        self.assertTrue(self.s.delete_user("u1"))
        self.assertIsNone(self.s.get_user("u1"))

    def test_local_cannot_be_deleted(self):
        ensure_local_user(self.s)
        with self.assertRaises(ValueError):
            self.s.delete_user("local")

    def test_bad_role_rejected(self):
        with self.assertRaises(ValueError):
            self.s.ensure_user("x", role="superuser")

    def test_prefs(self):
        ensure_local_user(self.s)
        self.s.set_pref("local", "theme", {"dark": True})
        self.assertEqual(self.s.get_pref("local", "theme"), {"dark": True})
        self.assertEqual(self.s.get_pref("local", "missing", "dflt"), "dflt")
        self.assertIn("theme", self.s.list_prefs("local"))

    def test_provider_config_refuses_keys(self):
        ensure_local_user(self.s)
        with self.assertRaises(ValueError):
            self.s.set_provider_config("local", "gemini",
                                       {"api_key": "SECRET"})
        with self.assertRaises(ValueError):
            self.s.set_provider_config("local", "gemini", {"token": "T"})
        self.s.set_provider_config("local", "gemini", {"enabled": False})
        self.assertEqual(
            self.s.get_provider_config("local", "gemini"), {"enabled": False})

    def test_permission_overrides(self):
        ensure_local_user(self.s)
        self.s.set_permission("local", "browser_open", "SAFE")
        self.assertEqual(self.s.get_permission("local", "browser_open"),
                         "SAFE")
        self.assertIn("browser_open", self.s.list_permissions("local"))
        self.assertTrue(self.s.clear_permission("local", "browser_open"))
        self.assertIsNone(self.s.get_permission("local", "browser_open"))
        with self.assertRaises(ValueError):
            self.s.set_permission("local", "x", "NUCLEAR")

    def test_conversations(self):
        ensure_local_user(self.s)
        cid = self.s.record_conversation(
            "local", [{"role": "user", "text": "hi", "ts": 1.0}],
            summary="greeting")
        convs = self.s.list_conversations("local")
        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]["id"], cid)
        self.assertEqual(convs[0]["messages"][0]["text"], "hi")
        self.assertTrue(self.s.delete_conversation("local", cid))
        self.assertEqual(self.s.list_conversations("local"), [])

    def test_automation_rules(self):
        ensure_local_user(self.s)
        r = self.s.add_rule("local", "morning", {"at": "08:00"},
                            {"say": "good morning"})
        self.assertTrue(r.enabled)
        self.assertEqual(len(self.s.list_rules("local")), 1)
        self.assertTrue(self.s.set_rule_enabled("local", r.id, False))
        self.assertFalse(self.s.list_rules("local")[0].enabled)
        self.assertTrue(self.s.delete_rule("local", r.id))
        self.assertEqual(self.s.list_rules("local"), [])


# ── Sessions ───────────────────────────────────────────────────────────────

class TestSessions(unittest.TestCase):
    def setUp(self):
        self.s = _store()
        ensure_local_user(self.s)
        self.m = SessionManager(self.s)

    def test_create_validate(self):
        tok = self.m.create("local", label="t")
        info = self.m.validate(tok)
        self.assertIsNotNone(info)
        self.assertEqual(info["user_id"], "local")
        self.assertEqual(info["label"], "t")

    def test_unknown_token_invalid(self):
        self.assertIsNone(self.m.validate("nope"))
        self.assertIsNone(self.m.validate(""))

    def test_expire(self):
        tok = self.m.create("local")
        self.assertTrue(self.m.expire(tok))
        self.assertIsNone(self.m.validate(tok))

    def test_revoke(self):
        tok = self.m.create("local")
        self.assertTrue(self.m.revoke(tok))
        self.assertIsNone(self.m.validate(tok))

    def test_revoke_all(self):
        t1 = self.m.create("local")
        t2 = self.m.create("local")
        n = self.m.revoke_all("local", except_token=t1)
        self.assertGreaterEqual(n, 1)
        self.assertIsNotNone(self.m.validate(t1))
        self.assertIsNone(self.m.validate(t2))

    def test_adopt_external_token(self):
        # Dashboard-minted token_urlsafe(32) bearers are adopted, not re-minted.
        import secrets as _s
        tok = _s.token_urlsafe(32)
        self.m.adopt("local", tok, session_key="PIN123", label="pin-login")
        info = self.m.validate(tok)
        self.assertIsNotNone(info)
        self.assertEqual(self.m.session_key_for(tok), "PIN123")

    def test_raw_token_never_in_db(self):
        tok = self.m.create("local")
        raw = (self.s.path).read_bytes()
        self.assertNotIn(tok.encode(), raw)

    def test_purge_expired(self):
        tok = self.m.create("local", ttl_hours=0.0001)
        time.sleep(0.05)
        # force expiry deterministically
        self.m.expire(tok)
        n = self.m.purge_expired()
        self.assertGreaterEqual(n, 1)
        self.assertEqual(self.m.list_sessions("local"), [])

    def test_list_sessions_truncates_hashes(self):
        tok = self.m.create("local")
        lst = self.m.list_sessions("local")
        self.assertEqual(len(lst), 1)
        self.assertNotIn(tok, str(lst))
        self.assertEqual(len(lst[0]["token_prefix"]), 12)


# ── Devices ────────────────────────────────────────────────────────────────

class TestDevices(unittest.TestCase):
    def setUp(self):
        self.s = _store()
        ensure_local_user(self.s)
        self.m = SessionManager(self.s)

    def test_register_find_list(self):
        dtok = self.m.register_device("local", name="Pixel", kind="phone",
                                      session_key="K1")
        dev = self.m.find_device(dtok)
        self.assertIsNotNone(dev)
        self.assertEqual(dev.name, "Pixel")
        self.assertEqual(dev.kind, "phone")
        devs = self.m.list_devices("local")
        self.assertEqual(len(devs), 1)
        # raw device token never recoverable from the listing
        self.assertNotIn(dtok, str([d.__dict__ for d in devs]))

    def test_device_login_mints_bearer(self):
        dtok = self.m.register_device("local", session_key="K2")
        bearer = self.m.device_login(dtok)
        self.assertIsNotNone(bearer)
        info = self.m.validate(bearer)
        self.assertEqual(info["kind"], "bearer")
        self.assertEqual(self.m.session_key_for(bearer), "K2")

    def test_unknown_device_login_fails(self):
        self.assertIsNone(self.m.device_login("bogus"))

    def test_revoke_device_cascades_to_bearers(self):
        dtok = self.m.register_device("local")
        bearer = self.m.device_login(dtok)
        dev = self.m.find_device(dtok)
        self.assertTrue(self.m.revoke_device(dev.id))
        self.assertIsNone(self.m.find_device(dtok))
        self.assertIsNone(self.m.validate(bearer))  # bearer died too

    def test_revoke_all_devices(self):
        self.m.register_device("local", name="a")
        self.m.register_device("local", name="b")
        n = self.m.revoke_all_devices("local")
        self.assertEqual(n, 2)
        self.assertEqual(self.m.list_devices("local"), [])

    def test_register_device_token_idempotent(self):
        # Adopting the dashboard's own minted device token (legacy compat).
        import secrets as _s
        dtok = _s.token_urlsafe(32)
        id1 = self.m.register_device_token("local", dtok, name="phone",
                                           session_key="K")
        id2 = self.m.register_device_token("local", dtok, name="phone",
                                           session_key="K")
        self.assertEqual(id1, id2)
        self.assertIsNotNone(self.m.device_login(dtok))


# ── Provider management (FREE-FIRST) ───────────────────────────────────────

class TestProviderManagement(unittest.TestCase):
    def setUp(self):
        self.s = _store()
        ensure_local_user(self.s)

    def test_effective_chain_defaults_global(self):
        self.assertEqual(prov.effective_chain(self.s, "local"),
                         ["gemini", "openrouter", "ollama"])

    def test_set_chain_and_primary(self):
        chain = prov.set_chain(self.s, "local", ["ollama", "gemini"],
                               actor_id="local")
        self.assertEqual(chain, ["ollama", "gemini"])
        self.assertEqual(prov.effective_chain(self.s, "local")[0], "ollama")
        chain2 = prov.set_primary(self.s, "local", "gemini", actor_id="local")
        self.assertEqual(chain2[0], "gemini")

    def test_chain_must_keep_a_free_tier(self):
        # A paid-only chain is refused even with opt-in.
        with self.assertRaises(ValueError):
            prov.set_chain(self.s, "local", ["nope-not-real"],
                           actor_id="local")

    def test_non_admin_cannot_manage(self):
        self.s.ensure_user("std", role=Role.STANDARD)
        with self.assertRaises(PermissionError):
            prov.set_enabled(self.s, "std", "ollama", False, actor_id="std")

    def test_unknown_provider_refused(self):
        with self.assertRaises(ValueError):
            prov.set_enabled(self.s, "local", "nope", True, actor_id="local")

    def test_free_first_paid_needs_opt_in(self):
        from core.providers.base import (AIProvider, ProviderResult,
                                         _PROVIDER_CLASSES,
                                         register_provider_class)
        orig_load = prov.load_global_config

        class FakePaid(AIProvider):
            name = "faketest_paid"
            tier = "Optional Paid Provider"
            capabilities = frozenset({"text"})

            def __init__(self, cfg=None):
                self._cfg = cfg or {}

            def is_available(self):
                return True, "ok"

            def complete(self, prompt, **kw):
                return ProviderResult("x", provider=self.name)

        register_provider_class("faketest_paid", FakePaid)
        prov.load_global_config = lambda: {
            "chain": ["gemini"], "providers": {
                "gemini": {"enabled": True, "tier": "Free API Tier",
                           "key_config": "gemini_api_key",
                           "key_env": "GEMINI_API_KEY"},
                "faketest_paid": {"enabled": False,
                                  "tier": "Optional Paid Provider",
                                  "key_config": "faketest_paid_key",
                                  "key_env": "FAKETEST_PAID_KEY"}}}
        try:
            # Paid enable without opt-in: refused.
            with self.assertRaises(PermissionError):
                prov.set_enabled(self.s, "local", "faketest_paid", True,
                                 actor_id="local")
            # Explicit opt-in, then enable works.
            self.assertTrue(prov.opt_in_paid(self.s, "local", actor_id="local",
                                            opt_in=True))
            r = prov.set_enabled(self.s, "local", "faketest_paid", True,
                                 actor_id="local")
            self.assertTrue(r["enabled"])
            # And a paid-only chain is still refused (free tier required).
            with self.assertRaises(ValueError):
                prov.set_chain(self.s, "local", ["faketest_paid"],
                               actor_id="local")
        finally:
            prov.load_global_config = orig_load
            _PROVIDER_CLASSES.pop("faketest_paid", None)

    def test_reserved_slot_cannot_be_enabled(self):
        # "groq" ships in providers.json with no implementation registered.
        with self.assertRaises(ValueError):
            prov.set_enabled(self.s, "local", "groq", True, actor_id="local")

    def test_provider_status_never_exposes_keys(self):
        os.environ["GEMINI_API_KEY"] = "test-env-key-123"
        try:
            for s in prov.provider_status(self.s, "local"):
                blob = json.dumps(s.__dict__)
                self.assertNotIn("test-env-key-123", blob)
            gem = [s for s in prov.provider_status(self.s, "local")
                   if s.name == "gemini"][0]
            self.assertTrue(gem.key_configured)
        finally:
            del os.environ["GEMINI_API_KEY"]

    def test_set_key_namespaced_and_deletable(self):
        from core.accounts.secrets import SecretStore
        st = SecretStore(backend="memory")
        # Patch the SecretStore that providers.set_key imports from
        # core.accounts.secrets (function-level import).
        orig = secrets_mod.SecretStore
        secrets_mod.SecretStore = lambda *a, **k: st
        try:
            self.assertTrue(prov.set_key(self.s, "local", "gemini",
                                        "K-SECRET", actor_id="local"))
            self.assertEqual(
                st.get("user:local:provider:gemini:gemini_api_key"),
                "K-SECRET")
            self.assertIsNone(st.get("gemini_api_key"))  # namespaced, not global
            self.assertTrue(prov.delete_key(self.s, "local", "gemini",
                                           actor_id="local"))
            self.assertIsNone(
                st.get("user:local:provider:gemini:gemini_api_key"))
        finally:
            secrets_mod.SecretStore = orig


# ── Per-user permissions ───────────────────────────────────────────────────

class TestPerUserPermissions(unittest.TestCase):
    def setUp(self):
        self.s = _store()
        ensure_local_user(self.s)
        self.s.ensure_user("std", role=Role.STANDARD)

    def test_default_matches_global(self):
        from core.permissions import default_level_for
        for tool in ("system_status", "delete_file", "shell_run"):
            self.assertEqual(uperm.effective_level(self.s, "local", tool),
                             default_level_for(tool))

    def test_override_raises_but_never_loosens(self):
        from core.permissions import Level, check as _gcheck
        uperm.set_override(self.s, "local", "local", "system_status",
                           "USER_CONFIRMATION")
        self.assertEqual(uperm.effective_level(self.s, "local",
                                               "system_status"),
                         Level.USER_CONFIRMATION)
        # Loosening attempt: global says USER_CONFIRMATION for delete_file;
        # a SAFE override must NOT make check() allow it silently.
        uperm.set_override(self.s, "local", "local", "delete_file", "SAFE")
        res = uperm.check(self.s, "local", "delete_file", {})
        self.assertTrue(res.needs_confirmation)

    def test_standard_user_privileged_denied(self):
        from core.permissions import Level
        # Even an explicit PRIVILEGED override can't arm a standard user.
        with self.assertRaises(PermissionError):
            uperm.set_override(self.s, "local", "std", "shell_run",
                               "PRIVILEGED")
        self.s.set_permission("std", "shell_run", "PRIVILEGED")
        lvl = uperm.effective_level(self.s, "std", "shell_run")
        self.assertEqual(lvl, Level.USER_CONFIRMATION)
        res = uperm.check(self.s, "std", "shell_run", {})
        self.assertTrue(res.needs_confirmation)

    def test_override_admin_only(self):
        with self.assertRaises(PermissionError):
            uperm.set_override(self.s, "std", "std", "system_status", "SAFE")

    def test_effective_policy_shape(self):
        pol = uperm.effective_policy(self.s, "local")
        self.assertIn("system_status", pol)
        self.assertEqual(pol["system_status"], "SAFE")
        self.assertEqual(pol["delete_file"], "USER_CONFIRMATION")


# ── Usage tracking ─────────────────────────────────────────────────────────

class TestUsage(unittest.TestCase):
    def setUp(self):
        self.s = _store()
        ensure_local_user(self.s)
        self.t = UsageTracker(self.s)

    def test_record_report_totals(self):
        self.t.record("local", "gemini", "requests", 3)
        self.t.record("local", "gemini", "errors", 1)
        self.t.record("local", "ollama", "requests", 2)
        rep = self.t.report("local")
        self.assertEqual(rep["providers"]["gemini"]["requests"], 3)
        self.assertEqual(rep["providers"]["gemini"]["errors"], 1)
        self.assertEqual(rep["providers"]["ollama"]["requests"], 2)
        # tokens stay 0 with an honesty note (no provider reports them)
        self.assertEqual(rep["providers"]["gemini"]["tokens_in"], 0)
        self.assertTrue(any("token" in n for n in rep["notes"]))
        totals = self.t.totals("local")
        self.assertEqual(totals["requests"], 5)
        self.assertEqual(totals["errors"], 1)

    def test_unknown_kind_ignored_never_raises(self):
        self.t.record("local", "gemini", "bogus-kind", 5)
        self.t.record("local", "gemini", "requests", -1)
        self.assertEqual(self.t.totals("local")["requests"], 0)

    def test_reset(self):
        self.t.record("local", "gemini", "requests", 2)
        self.assertGreater(self.t.reset("local", provider="gemini"), 0)
        self.assertEqual(self.t.totals("local")["requests"], 0)

    def test_registry_hook_records(self):
        from core.providers import registry
        from core.providers.base import (AIProvider, ProviderError,
                                         ProviderResult, RateLimitError)

        class Good(AIProvider):
            name = "good"
            tier = "Free API Tier"
            capabilities = frozenset({"text"})

            def __init__(self, cfg=None):
                pass

            def is_available(self):
                return True, "ok"

            def complete(self, prompt, **kw):
                return ProviderResult("ok", provider="good")

        class Bad(AIProvider):
            name = "bad"
            tier = "Free API Tier"
            capabilities = frozenset({"text"})

            def __init__(self, cfg=None):
                pass

            def is_available(self):
                return True, "ok"

            def complete(self, prompt, **kw):
                raise RateLimitError("429")

        orig_chain, orig_get, orig_rec = (registry.chain, registry.get,
                                         registry._usage_recorder)
        registry.chain = lambda: ["bad", "good"]
        registry.get = lambda name, refresh=False: {"bad": Bad(),
                                                    "good": Good()}[name]
        install_registry_recorder(self.s, "local")
        try:
            r = registry.generate("hi", user_id="local")
            self.assertEqual(r.provider, "good")
            rep = self.t.report("local")["providers"]
            self.assertEqual(rep["bad"]["requests"], 1)
            self.assertEqual(rep["bad"]["errors"], 1)
            self.assertEqual(rep["good"]["requests"], 1)
            self.assertEqual(rep["good"]["errors"], 0)
        finally:
            registry.chain, registry.get = orig_chain, orig_get
            registry._usage_recorder = orig_rec

    def test_recorder_never_breaks_generate(self):
        from core.providers import registry

        def boom(*a, **k):
            raise RuntimeError("tracker down")

        orig = registry._usage_recorder
        registry._usage_recorder = boom
        try:
            registry._record_usage("local", "gemini", "requests")
        finally:
            registry._usage_recorder = orig


# ── Encrypted secret store ─────────────────────────────────────────────────

class TestSecretStore(unittest.TestCase):
    def test_memory_backend_roundtrip(self):
        from core.accounts.secrets import SecretStore
        st = SecretStore(backend="memory")
        st.set("k1", "v1")
        self.assertEqual(st.get("k1"), "v1")
        self.assertTrue(st.has("k1"))
        self.assertEqual(st.list_names(), ["k1"])
        self.assertTrue(st.delete("k1"))
        self.assertIsNone(st.get("k1"))

    def test_fail_closed_without_backend(self):
        from core.accounts.secrets import SecretStore
        st = SecretStore(backend="memory")
        st._backends = []  # simulate: no keyring, no cryptography
        self.assertFalse(st.has_secure_backend)
        with self.assertRaises(RuntimeError):
            st.set("k", "v")

    def test_resolve_key_env_first(self):
        os.environ["SHIRAZI_SECRET_MY_KEY"] = "env-wins"
        try:
            v = secrets_mod.resolve_key(
                "my_key", None,
                store=secrets_mod.SecretStore(backend="memory"))
            self.assertEqual(v, "env-wins")
        finally:
            del os.environ["SHIRAZI_SECRET_MY_KEY"]

    def test_resolve_key_user_namespaced(self):
        from core.accounts.secrets import SecretStore
        st = SecretStore(backend="memory")
        st.set("user:u1:provider:gemini:gemini_api_key", "USERKEY")
        st.set("gemini_api_key", "GLOBALKEY")
        self.assertEqual(
            secrets_mod.resolve_key("gemini_api_key", "GEMINI_API_KEY",
                                    user_id="u1", provider="gemini", store=st),
            "USERKEY")
        self.assertEqual(
            secrets_mod.resolve_key("gemini_api_key", "GEMINI_API_KEY",
                                    store=st),
            "GLOBALKEY")

    def test_legacy_plaintext_fallback(self):
        d = _tmpdir()
        (d / "config").mkdir()
        (d / "config" / "api_keys.json").write_text(
            json.dumps({"gemini_api_key": "LEGACY-PLAIN"}))
        orig = secrets_mod._API_KEYS_FILE
        secrets_mod._API_KEYS_FILE = d / "config" / "api_keys.json"
        try:
            v = secrets_mod.resolve_key(
                "gemini_api_key", "GEMINI_API_KEY",
                store=secrets_mod.SecretStore(backend="memory"))
            self.assertEqual(v, "LEGACY-PLAIN")
        finally:
            secrets_mod._API_KEYS_FILE = orig

    @unittest.skipUnless(_CRYPTO, "cryptography not installed")
    def test_encrypted_file_backend_no_residue(self):
        from core.accounts.secrets import SecretStore
        d = _tmpdir()
        (d / "config").mkdir()
        st = SecretStore(base_dir=d)  # auto: keyring unusable here → file
        self.assertIn("encrypted-file", st.backend_names)
        st.set("gemini_api_key", "TOP-SECRET-VALUE-XYZ")
        self.assertEqual(st.get("gemini_api_key"), "TOP-SECRET-VALUE-XYZ")
        raw = (d / "config" / "credentials.enc").read_bytes()
        self.assertNotIn(b"TOP-SECRET-VALUE-XYZ", raw)
        self.assertNotIn(b"gemini_api_key", raw)  # names encrypted too
        # data key file is restricted
        mode = (d / "config" / ".secret_key").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        # survives a fresh instance (persistence)
        st2 = SecretStore(base_dir=d)
        self.assertEqual(st2.get("gemini_api_key"), "TOP-SECRET-VALUE-XYZ")


class TestMigration(unittest.TestCase):
    def _api_keys(self, d, data):
        (d / "config").mkdir(exist_ok=True)
        (d / "config" / "api_keys.json").write_text(json.dumps(data))
        return d / "config" / "api_keys.json"

    def _memstore(self):
        from core.accounts.secrets import SecretStore
        return SecretStore(backend="memory")

    def test_migrate_moves_secrets_keeps_prefs(self):
        from core.accounts.secrets import SecretStore
        d = _tmpdir()
        p = self._api_keys(d, {
            "gemini_api_key": "K1", "openrouter_api_key": "K2",
            "assistant_name": "SHIRAZI", "voice_name": "Charon",
            "plugin_config": {"x": {"token": "T"}}})
        st = SecretStore(backend="memory")
        rep = secrets_mod.migrate_api_keys_json(base_dir=d, store=st)
        self.assertTrue(rep["migrated"])
        self.assertEqual(sorted(rep["moved"]),
                         ["gemini_api_key", "openrouter_api_key",
                          "plugin_config"])
        self.assertTrue(Path(rep["backup"]).exists())
        after = json.loads(p.read_text())
        self.assertNotIn("gemini_api_key", after)
        self.assertNotIn("openrouter_api_key", after)
        self.assertNotIn("plugin_config", after)
        # prefs untouched
        self.assertEqual(after["assistant_name"], "SHIRAZI")
        self.assertEqual(after["voice_name"], "Charon")
        self.assertTrue(after["_secrets_migrated"])
        # values recoverable from the store
        self.assertEqual(st.get("gemini_api_key"), "K1")
        self.assertEqual(st.get("plugin_config"), '{"x": {"token": "T"}}')
        # no plaintext residue
        self.assertEqual(secrets_mod.scan_plaintext_residue(base_dir=d), [])

    def test_migrate_idempotent(self):
        d = _tmpdir()
        self._api_keys(d, {"gemini_api_key": "K1", "assistant_name": "S"})
        st = self._memstore()
        r1 = secrets_mod.migrate_api_keys_json(base_dir=d, store=st)
        self.assertTrue(r1["migrated"])
        r2 = secrets_mod.migrate_api_keys_json(base_dir=d, store=st)
        self.assertFalse(r2["migrated"])
        self.assertEqual(r2["reason"], "already-migrated")
        # third run after a NEW secret appears migrates just the delta
        p = d / "config" / "api_keys.json"
        data = json.loads(p.read_text())
        data["groq_api_key"] = "K9"
        p.write_text(json.dumps(data))
        r3 = secrets_mod.migrate_api_keys_json(base_dir=d, store=st)
        self.assertTrue(r3["migrated"])
        self.assertEqual(r3["moved"], ["groq_api_key"])
        self.assertEqual(st.get("groq_api_key"), "K9")

    def test_migrate_no_file(self):
        d = _tmpdir()
        (d / "config").mkdir()
        rep = secrets_mod.migrate_api_keys_json(base_dir=d,
                                                store=self._memstore())
        self.assertFalse(rep["migrated"])
        self.assertEqual(rep["reason"], "no api_keys.json")

    def test_migrate_fail_closed_without_backend(self):
        from core.accounts.secrets import SecretStore
        d = _tmpdir()
        self._api_keys(d, {"gemini_api_key": "K1"})
        st = SecretStore(backend="memory")
        st._backends = []
        rep = secrets_mod.migrate_api_keys_json(base_dir=d, store=st)
        self.assertFalse(rep["migrated"])
        self.assertIn("backend-unavailable", rep["reason"])
        # plaintext left untouched (no data loss, no pretend-migration)
        after = json.loads((d / "config" / "api_keys.json").read_text())
        self.assertEqual(after["gemini_api_key"], "K1")
        self.assertNotIn("_secrets_migrated", after)

    def test_migrate_never_overwrites_original_backup(self):
        d = _tmpdir()
        self._api_keys(d, {"gemini_api_key": "K1"})
        st = self._memstore()
        r1 = secrets_mod.migrate_api_keys_json(base_dir=d, store=st)
        bak1 = Path(r1["backup"])
        self.assertEqual(bak1.name, "api_keys.json.bak")
        first = bak1.read_bytes()
        # add another secret later; second backup must not clobber the first
        p = d / "config" / "api_keys.json"
        data = json.loads(p.read_text())
        data["x_api_key"] = "K2"
        p.write_text(json.dumps(data))
        r2 = secrets_mod.migrate_api_keys_json(base_dir=d, store=st)
        self.assertNotEqual(Path(r2["backup"]).name, "api_keys.json.bak")
        self.assertEqual(bak1.read_bytes(), first)


# ── Dashboard wiring ───────────────────────────────────────────────────────

@unittest.skipUnless(_FASTAPI, "fastapi not installed")
class TestDashboardWiring(unittest.TestCase):
    def setUp(self):
        d = _tmpdir()
        self.store = AccountStore(db_path=d / "acct.db")
        self.srv = DashboardServer(account_store=self.store)
        self.client = TestClient(self.srv.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _login(self, pin="P8TEST"):
        self.srv._pending_keys[pin] = time.time() + 600
        r = self.client.post("/login", json={"pin": pin})
        self.assertEqual(r.status_code, 200)
        return r.json()["token"]

    def test_auth_is_single_user_instance_of_sessions(self):
        tok = self._login()
        # Adopted into the general mechanism...
        self.assertIsNotNone(self.srv._sessions.validate(tok))
        # ...so it validates even when the legacy in-memory set is cleared
        # (e.g. after adopting, or across a restart via the db).
        self.srv._tokens.discard(tok)
        self.srv._token_keys.pop(tok, None)
        r = self.client.get("/api/v1/me",
                            headers={"Authorization": f"Bearer {tok}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["id"], "local")

    def test_device_login_falls_back_to_registry(self):
        # Pair via QR path, then simulate a restart: clear in-memory maps.
        key = self.srv.new_key()
        self.srv._pending_keys[key] = time.time() + 600
        # call auto-login handler directly is awkward; emulate its effects:
        import secrets as _s
        tok, dev_tok = _s.token_urlsafe(32), _s.token_urlsafe(32)
        self.srv._tokens.add(tok)
        self.srv._token_keys[tok] = key
        self.srv._device_sessions[dev_tok] = {"session_key": key}
        self.srv._register_session(tok, key, label="qr-pairing")
        self.srv._sessions.register_device_token(
            self.srv._local_user_id, dev_tok, name="phone",
            kind="phone", session_key=key)
        # "restart": wipe in-memory device map (db registry survives)
        self.srv._device_sessions.clear()
        r = self.client.post("/api/device-login",
                             json={"device_token": dev_tok})
        self.assertEqual(r.status_code, 200)
        new_tok = r.json()["token"]
        r2 = self.client.get("/api/v1/me",
                             headers={"Authorization": f"Bearer {new_tok}"})
        self.assertEqual(r2.status_code, 200)

    def test_legacy_device_token_value_still_works(self):
        # A Mark-LIV-era jarvis_device_token value validates by token value.
        import secrets as _s
        dev_tok = _s.token_urlsafe(32)  # the "legacy" value
        self.srv._device_sessions[dev_tok] = {"session_key": "LEGACYKEY"}
        r = self.client.post("/api/device-login",
                             json={"device_token": dev_tok})
        self.assertEqual(r.status_code, 200)

    def test_v1_auth_gates(self):
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/devices").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/usage").status_code, 401)
        self.assertEqual(
            self.client.get("/api/v1/me",
                            headers={"Authorization": "Bearer bogus"}).status_code,
            401)

    def test_v1_providers_no_key_values(self):
        tok = self._login()
        H = {"Authorization": f"Bearer {tok}"}
        r = self.client.get("/api/v1/providers", headers=H)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("chain", body)
        blob = json.dumps(body["providers"])
        for p in body["providers"]:
            self.assertIn("key_configured", p)
            self.assertNotIn("api_key", [k for k in p])
        # keys can never be smuggled in through the phone API
        r = self.client.post("/api/v1/providers/config", headers=H,
                             json={"provider": "gemini", "api_key": "EVIL"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/v1/providers/config", headers=H,
                             json={"provider": "gemini", "token": "EVIL"})
        self.assertEqual(r.status_code, 400)

    def test_v1_admin_gates(self):
        tok = self._login()
        H = {"Authorization": f"Bearer {tok}"}
        # local is admin: user creation works
        r = self.client.post("/api/v1/users", headers=H,
                             json={"id": "std1", "role": "standard"})
        self.assertEqual(r.status_code, 200)
        # mint a token for the standard user; admin routes must deny it
        sm = self.srv._sessions
        std_tok = sm.create("std1", label="t")
        H2 = {"Authorization": f"Bearer {std_tok}"}
        self.assertEqual(self.client.get("/api/v1/me", headers=H2).status_code,
                         200)
        self.assertEqual(
            self.client.post("/api/v1/users", headers=H2,
                             json={"id": "x", "role": "standard"}).status_code,
            403)
        self.assertEqual(
            self.client.post("/api/v1/providers/config", headers=H2,
                             json={"provider": "ollama",
                                   "enabled": False}).status_code,
            403)

    def test_v1_devices_crud(self):
        tok = self._login()
        H = {"Authorization": f"Bearer {tok}"}
        r = self.client.post("/api/v1/devices", headers=H,
                             json={"kind": "tablet", "name": "Tab"})
        self.assertEqual(r.status_code, 200)
        dev = r.json()
        self.assertTrue(dev["device_token"])  # returned once
        devs = self.client.get("/api/v1/devices", headers=H).json()["devices"]
        self.assertEqual(len(devs), 1)
        self.assertNotIn("device_token", devs[0])  # never listed
        r = self.client.delete(f"/api/v1/devices/{dev['device_id']}",
                               headers=H)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(
            self.client.get("/api/v1/devices", headers=H).json()["devices"],
            [])

    def test_v1_usage_and_permissions(self):
        tok = self._login()
        H = {"Authorization": f"Bearer {tok}"}
        self.srv._usage.record("local", "gemini", "requests", 4)
        rep = self.client.get("/api/v1/usage?days=7", headers=H).json()
        self.assertEqual(rep["providers"]["gemini"]["requests"], 4)
        self.assertTrue(rep["notes"])
        perms = self.client.get("/api/v1/permissions", headers=H).json()
        self.assertEqual(perms["role"], "admin")
        self.assertEqual(perms["effective"]["system_status"], "SAFE")

    def test_v1_logout_revokes(self):
        tok = self._login()
        H = {"Authorization": f"Bearer {tok}"}
        self.assertEqual(
            self.client.post("/api/v1/logout", headers=H).status_code, 200)
        self.assertEqual(
            self.client.get("/api/v1/me", headers=H).status_code, 401)

    def test_v1_health_no_auth(self):
        r = self.client.get("/api/v1/health")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["accounts"])


if __name__ == "__main__":
    unittest.main()


# ── config_manager save/get: desktop-flow safety ─────────────────────────
class TestConfigManagerSave(unittest.TestCase):
    """save_api_keys must keep the legacy plaintext copy: twelve action
    modules read config/api_keys.json directly with no fallback chain,
    so scrubbing it on save would break the single-user desktop flow."""

    def setUp(self):
        import memory.config_manager as cm
        self.cm = cm
        self.d = _tmpdir()
        self._orig_dir = cm.CONFIG_DIR
        self._orig_file = cm.CONFIG_FILE
        self._orig_store_fn = cm._secret_store
        cm.CONFIG_DIR = self.d
        cm.CONFIG_FILE = self.d / "api_keys.json"
        from core.accounts.secrets import SecretStore
        self.mem = SecretStore(backend="memory")
        cm._secret_store = lambda: self.mem

    def tearDown(self):
        self.cm.CONFIG_DIR = self._orig_dir
        self.cm.CONFIG_FILE = self._orig_file
        self.cm._secret_store = self._orig_store_fn

    def test_save_writes_store_and_keeps_plaintext(self):
        self.cm.save_api_keys("K-NEW")
        self.assertEqual(self.mem.get("gemini_api_key"), "K-NEW")
        # Legacy readers must still find it in the plaintext file.
        data = json.loads((self.d / "api_keys.json").read_text())
        self.assertEqual(data.get("gemini_api_key"), "K-NEW")

    def test_save_without_backend_plaintext_only(self):
        self.cm._secret_store = lambda: None
        self.cm.save_api_keys("K-PLAIN")
        data = json.loads((self.d / "api_keys.json").read_text())
        self.assertEqual(data.get("gemini_api_key"), "K-PLAIN")

    def test_get_secret_prefers_store_then_plaintext(self):
        self.mem.set("gemini_api_key", "K-STORE")
        (self.d / "api_keys.json").write_text(
            json.dumps({"gemini_api_key": "K-OLD"}))
        self.assertEqual(self.cm.get_secret("gemini_api_key"), "K-STORE")
        self.mem.delete("gemini_api_key")
        self.assertEqual(self.cm.get_secret("gemini_api_key"), "K-OLD")

    def test_delete_secret_removes_both_copies(self):
        self.mem.set("gemini_api_key", "K-X")
        (self.d / "api_keys.json").write_text(
            json.dumps({"gemini_api_key": "K-X", "theme": "dark"}))
        self.assertTrue(self.cm.delete_secret("gemini_api_key"))
        self.assertIsNone(self.mem.get("gemini_api_key"))
        data = json.loads((self.d / "api_keys.json").read_text())
        self.assertNotIn("gemini_api_key", data)
        self.assertEqual(data.get("theme"), "dark")  # prefs untouched
