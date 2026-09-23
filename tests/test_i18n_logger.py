"""Unit tests for i18n (loading, fallback, RTL) and core/logger (redaction)."""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import i18n
from i18n import t, set_language, is_rtl, rtl_mark
from core import logger
from core.logger import redact


class TestI18n(unittest.TestCase):
    def tearDown(self):
        set_language("en")

    def test_four_languages_available(self):
        self.assertEqual(set(i18n.available()), {"en", "ur", "ar", "ur-Latn"})

    def test_english_defaults(self):
        set_language("en")
        self.assertEqual(t("listening"), "Listening...")
        self.assertEqual(t("app_name"), "Shirazi")

    def test_urdu(self):
        set_language("ur")
        out = t("listening")
        self.assertIn("سن", out)
        self.assertTrue(is_rtl())

    def test_arabic(self):
        set_language("ar")
        self.assertIn("أستمع", t("listening"))
        self.assertTrue(is_rtl())

    def test_roman_urdu(self):
        set_language("ur-Latn")
        self.assertIn("Sun raha", t("listening"))
        self.assertFalse(is_rtl())  # Latin script is LTR

    def test_placeholders(self):
        set_language("en")
        out = t("confirm_title", tool="browser_control")
        self.assertIn("browser_control", out)

    def test_missing_key_returns_key(self):
        self.assertEqual(t("no_such_key_xyz"), "no_such_key_xyz")

    def test_fallback_to_english(self):
        # ur catalog has every key; simulate a gap by asking for an en-only
        # shape via a bogus language override path — simpler: unknown lang → en
        set_language("xx-unknown")
        self.assertEqual(i18n.current_language(), "en")
        self.assertEqual(t("listening"), "Listening...")

    def test_rtl_mark(self):
        marked = rtl_mark("سلام", lang="ur")
        self.assertTrue(marked.startswith("\u2067") and marked.endswith("\u2069"))
        plain = rtl_mark("hello", lang="en")
        self.assertEqual(plain, "hello")

    def test_catalogs_have_same_keys(self):
        import json
        base = set(json.loads((Path(i18n.__file__).parent / "en.json")
                              .read_text(encoding="utf-8"))["strings"])
        for lang in ("ur", "ar", "ur-Latn"):
            keys = set(json.loads((Path(i18n.__file__).parent / f"{lang}.json")
                                  .read_text(encoding="utf-8"))["strings"])
            missing = base - keys
            self.assertEqual(missing, set(), f"{lang} missing keys: {missing}")


class TestLogger(unittest.TestCase):
    def test_redact_api_key_assignment(self):
        out = redact('config has api_key="sk-secret-1234567890abcdef" done')
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("sk-secret", out)

    def test_redact_bearer(self):
        out = redact("Authorization: Bearer abcdefghijklmnop123456")
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("abcdefghijklmnop", out)

    def test_redact_password(self):
        out = redact("login with password=hunter2-hunter2-hunter2 ok")
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("hunter2", out)

    def test_redact_token_key(self):
        out = redact("{'token': 'ya29.verylongsecrettokenvalue123456'}")
        self.assertIn("[REDACTED]", out)
        self.assertNotIn("ya29", out)

    def test_normal_text_untouched(self):
        msg = "Listening on device USB Mic at 16000 Hz"
        self.assertEqual(redact(msg), msg)

    def test_tag_functions_do_not_raise(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            logger.listening("test")
            logger.speaking("hello")
            logger.thinking("provider=gemini")
            logger.searching("q")
            logger.executing("tool")
            logger.file_op("/tmp/x")
            logger.system("boot")
            logger.device("mic")
            logger.completed("done")
            logger.warn("tools", "careful")
            logger.info("note")
        out = buf.getvalue()
        for tag in ("🎤", "🔊", "🧠", "🌐", "🖥️", "📁", "⚙️", "🔌", "✅", "⚠️"):
            self.assertIn(tag, out)

    def test_error_goes_to_stderr(self):
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            logger.error("tools", "boom")
        self.assertIn("❌", err.getvalue())

    def test_handlers_receive_redacted(self):
        seen = []
        logger.add_handler(lambda tag, msg: seen.append((tag, msg)))
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                logger.info('api_key="supersecretvalue1234567890"')
        finally:
            logger._handlers.pop()
        self.assertTrue(seen)
        self.assertIn("[REDACTED]", seen[0][1])
        self.assertNotIn("supersecret", seen[0][1])


if __name__ == "__main__":
    unittest.main()
