"""Unit tests for core/providers — mocked transports, no network."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.providers import base, registry
from core.providers.base import (AIProvider, ProviderError, AuthError,
                                 RateLimitError, ModelUnavailableError,
                                 NetworkError, OutageError, ProviderResult)
from core.providers.openrouter import OpenRouterProvider
from core.providers.ollama import OllamaProvider


class FakeProvider(AIProvider):
    """Scripted provider for chain tests."""
    name = "fake"
    tier = "Local (Free)"
    capabilities = frozenset({"text"})

    def __init__(self, cfg=None, script=None):
        self._script = script or []
        self.calls = 0

    def is_available(self):
        return True, "ok"

    def complete(self, prompt, *, system="", timeout_s=30.0, json_mode=False):
        self.calls += 1
        action = self._script[min(self.calls - 1, len(self._script) - 1)]
        if isinstance(action, BaseException):
            raise action
        return ProviderResult(text=action, provider="fake", model="fake-1")


def _transport(status, data):
    def _t(url, headers, payload, timeout):
        return status, data
    return _t


class TestErrorTaxonomy(unittest.TestCase):
    def test_classes_distinct(self):
        for cls in (AuthError, RateLimitError, ModelUnavailableError,
                    NetworkError, OutageError):
            self.assertTrue(issubclass(cls, ProviderError))

    def test_result_bool(self):
        self.assertTrue(ProviderResult(text="hi"))
        self.assertFalse(ProviderResult(text=""))


class TestOpenRouter(unittest.TestCase):
    def _cfg(self, models=(":free-ok:free",)):
        return {"enabled": True, "key_env": "SHIRAZI_TEST_OR_KEY",
                "models": list(models), "free_only": True}

    def test_no_key_is_auth_error(self):
        import os
        os.environ.pop("SHIRAZI_TEST_OR_KEY", None)
        p = OpenRouterProvider(self._cfg(), transport=_transport(200, {}))
        with self.assertRaises(AuthError):
            p.complete("hi")

    def test_success(self):
        import os
        os.environ["SHIRAZI_TEST_OR_KEY"] = "test-key"
        body = {"choices": [{"message": {"content": "hello"}}]}
        p = OpenRouterProvider(self._cfg(), transport=_transport(200, body))
        r = p.complete("hi")
        self.assertEqual(r.text, "hello")
        self.assertEqual(r.provider, "openrouter")
        self.assertTrue(r.model.endswith(":free"))
        del os.environ["SHIRAZI_TEST_OR_KEY"]

    def test_free_only_rejects_billable(self):
        import os
        os.environ["SHIRAZI_TEST_OR_KEY"] = "test-key"
        p = OpenRouterProvider(self._cfg(models=("some-model",)),
                               transport=_transport(200, {}))
        with self.assertRaises(ModelUnavailableError):
            p.complete("hi")
        del os.environ["SHIRAZI_TEST_OR_KEY"]

    def test_429_maps_to_rate_limit(self):
        import os
        os.environ["SHIRAZI_TEST_OR_KEY"] = "test-key"
        p = OpenRouterProvider(self._cfg(), transport=_transport(429, {"error": "slow down"}))
        with self.assertRaises(RateLimitError):
            p.complete("hi")
        del os.environ["SHIRAZI_TEST_OR_KEY"]

    def test_401_maps_to_auth(self):
        import os
        os.environ["SHIRAZI_TEST_OR_KEY"] = "bad-key"
        p = OpenRouterProvider(self._cfg(), transport=_transport(401, {"error": "bad key"}))
        with self.assertRaises(AuthError):
            p.complete("hi")
        del os.environ["SHIRAZI_TEST_OR_KEY"]

    def test_404_maps_to_model_unavailable(self):
        import os
        os.environ["SHIRAZI_TEST_OR_KEY"] = "test-key"
        p = OpenRouterProvider(self._cfg(), transport=_transport(404, {"error": "nope"}))
        with self.assertRaises(ModelUnavailableError):
            p.complete("hi")
        del os.environ["SHIRAZI_TEST_OR_KEY"]


class TestGeminiProvider(unittest.TestCase):
    def test_no_key_is_auth_error(self):
        from core.providers.gemini import GeminiProvider, _classify
        # No config/api_keys.json in the test env → api_key() == "" → AuthError
        p = GeminiProvider({"text_models": ["gemini-3.6-flash"]})
        ok, reason = p.is_available()
        if not ok:
            with self.assertRaises(AuthError):
                p.complete("hi")
        else:
            self.skipTest("a real Gemini key is configured in this env")

    def test_classify(self):
        from core.providers.gemini import _classify
        self.assertIsInstance(_classify(Exception("401 Unauthorized")), AuthError)
        self.assertIsInstance(_classify(Exception("429 RESOURCE_EXHAUSTED")), RateLimitError)
        self.assertIsInstance(_classify(Exception("404 model not found")), ModelUnavailableError)
        self.assertIsInstance(_classify(Exception("connection timed out")), NetworkError)
        self.assertIsInstance(_classify(Exception("weird 500 thing")), OutageError)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        registry.reset()
        self._orig_chain = registry.chain
        self._orig_get = registry.get

    def tearDown(self):
        registry.chain = self._orig_chain
        registry.get = self._orig_get
        registry.reset()

    def test_failover_primary_to_fallback(self):
        bad = FakeProvider(script=[RateLimitError("429")])
        good = FakeProvider(script=["fallback answer"])
        registry.chain = lambda: ["bad", "good"]
        registry.get = lambda name, refresh=False: {"bad": bad, "good": good}[name]
        r = registry.generate("hi")
        self.assertEqual(r.text, "fallback answer")
        self.assertTrue(r.used_fallback)
        self.assertEqual(bad.calls, 1)
        self.assertEqual(good.calls, 1)

    def test_total_failure_returns_honest_message_never_raises(self):
        bad = FakeProvider(script=[OutageError("down")])
        registry.chain = lambda: ["bad"]
        registry.get = lambda name, refresh=False: bad
        r = registry.generate("hi")  # must not raise
        self.assertIsNotNone(r)
        self.assertIn("couldn't get an answer", r.text)
        self.assertNotIn("Traceback", r.text)

    def test_no_providers_configured(self):
        registry.chain = lambda: []
        registry.get = lambda name, refresh=False: None
        r = registry.generate("hi")
        self.assertIn("none is configured", r.text)

    def test_skip_list(self):
        good = FakeProvider(script=["answer"])
        registry.chain = lambda: ["good"]
        registry.get = lambda name, refresh=False: good
        r = registry.generate("hi", skip=("good",))
        self.assertIn("couldn't reach any AI provider", r.text)
        self.assertEqual(good.calls, 0)

    def test_ollama_probe_never_raises(self):
        p = OllamaProvider({"enabled": True})
        ok, reason = p.is_available()  # must not raise even with no ollama
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(reason, str)


if __name__ == "__main__":
    unittest.main()
