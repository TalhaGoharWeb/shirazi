"""Unit tests for core/memory (SQLite layers, incl. delete) and core/events."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.memory.store import MemoryStore, LAYERS
from core import events


class TestMemoryStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(db_path=Path(self._tmp.name) / "test.db")

    def tearDown(self):
        self._tmp.cleanup()

    def test_layers_exist(self):
        self.assertEqual(set(LAYERS), {"session", "user", "tool", "research"})

    def test_remember_and_recall(self):
        self.store.remember("user", "name", "Ali")
        self.assertEqual(self.store.recall("user", "name"), "Ali")
        self.assertIsNone(self.store.recall("user", "missing"))
        self.assertEqual(self.store.recall("user", "missing", default="d"), "d")

    def test_remember_complex_values(self):
        self.store.remember("tool", "mic", {"name": "USB Mic", "rate": 16000})
        self.assertEqual(self.store.recall("tool", "mic")["rate"], 16000)

    def test_overwrite(self):
        self.store.remember("session", "k", "v1")
        self.store.remember("session", "k", "v2")
        self.assertEqual(self.store.recall("session", "k"), "v2")

    def test_search(self):
        self.store.remember("research", "topic-a", "quantum dots are tiny")
        self.store.remember("research", "topic-b", "unrelated")
        hits = self.store.search("research", "quantum")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["key"], "topic-a")

    def test_delete(self):
        self.store.remember("user", "temp", "x")
        self.assertTrue(self.store.delete("user", "temp"))
        self.assertIsNone(self.store.recall("user", "temp"))
        self.assertFalse(self.store.delete("user", "temp"))  # already gone

    def test_wipe(self):
        self.store.remember("session", "a", "1")
        self.store.remember("session", "b", "2")
        self.assertEqual(self.store.wipe("session"), 2)
        self.assertEqual(self.store.list("session"), [])

    def test_inspect_and_counts(self):
        self.store.remember("user", "k1", "v")
        insp = self.store.inspect()
        self.assertEqual(set(insp.keys()), set(LAYERS))
        self.assertEqual(len(insp["user"]), 1)
        self.assertEqual(self.store.counts()["user"], 1)

    def test_export_json(self):
        self.store.remember("user", "k", "v")
        exp = self.store.export_json()
        self.assertEqual(exp["user"][0]["value"], "v")

    def test_invalid_layer_raises(self):
        with self.assertRaises(ValueError):
            self.store.remember("nope", "k", "v")
        with self.assertRaises(ValueError):
            self.store.recall("nope", "k")

    def test_empty_key_raises(self):
        with self.assertRaises(ValueError):
            self.store.remember("user", "   ", "v")

    def test_layers_isolated(self):
        self.store.remember("user", "k", "user-v")
        self.store.remember("session", "k", "session-v")
        self.assertEqual(self.store.recall("user", "k"), "user-v")
        self.assertEqual(self.store.recall("session", "k"), "session-v")

    def test_metadata_roundtrip(self):
        self.store.remember("user", "k", "v", metadata={"source": "test"})
        entries = self.store.list("user")
        self.assertEqual(entries[0]["metadata"]["source"], "test")


class TestMemoryAdapters(unittest.TestCase):
    def test_sync_never_raises(self):
        # Legacy store may or may not exist in the test env — either way, no crash.
        from core.memory import adapters
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(db_path=Path(td) / "t.db")
            n = adapters.sync_legacy_to_user_layer(store)
            self.assertIsInstance(n, int)
            self.assertGreaterEqual(n, 0)

    def test_user_memory_for_prompt_never_raises(self):
        from core.memory import adapters
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(db_path=Path(td) / "t.db")
            out = adapters.user_memory_for_prompt(store)
            self.assertIsInstance(out, str)


class TestEventBus(unittest.TestCase):
    def setUp(self):
        events.reset()

    def tearDown(self):
        events.reset()

    def test_subscribe_emit_unsubscribe(self):
        got = []
        unsub = events.subscribe("speech.speaking", lambda p: got.append(p))
        n = events.emit("speech.speaking", {"text": "hi"})
        self.assertEqual(n, 1)
        self.assertEqual(got[0]["text"], "hi")
        self.assertEqual(got[0]["topic"], "speech.speaking")
        self.assertIn("ts", got[0])
        unsub()
        self.assertEqual(events.subscriber_count("speech.speaking"), 0)
        self.assertEqual(events.emit("speech.speaking", {}), 0)

    def test_bad_handler_does_not_break_bus(self):
        order = []

        def bad(p):
            raise RuntimeError("boom")

        events.subscribe("warning", bad)
        events.subscribe("warning", lambda p: order.append("good"))
        n = events.emit("warning", {"message": "x"})
        self.assertEqual(n, 1)  # only the good one counted
        self.assertEqual(order, ["good"])

    def test_unknown_topic_allowed_but_logged(self):
        # Emitting an off-contract topic must not raise.
        self.assertEqual(events.emit("my.prototype.topic", {}), 0)
        self.assertIn("my.prototype.topic", events.stats())

    def test_stats(self):
        events.emit("thinking.start", {})
        events.emit("thinking.start", {})
        self.assertEqual(events.stats()["thinking.start"], 2)


if __name__ == "__main__":
    unittest.main()
