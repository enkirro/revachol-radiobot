import io
import unittest
import urllib.error
import urllib.parse
from pathlib import Path
from unittest import mock

from helpers import make_settings, tmpdir

from revachol_radiobot import notify
from revachol_radiobot.config import ConfigError
from revachol_radiobot.notify import Notifier


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tmpdir()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_sends_message_to_chat(self):
        settings = make_settings(
            self.tmp,
            RADIOBOT_TELEGRAM_BOT_TOKEN="123:ABC",
            RADIOBOT_TELEGRAM_CHAT_ID="42",
        )
        with mock.patch.object(notify, "_request") as req:
            ok = Notifier(settings).send("Sesión caducada", "renueva las cookies")
        self.assertTrue(ok)
        url, data, _ = req.call_args.args
        self.assertEqual(url, "https://api.telegram.org/bot123:ABC/sendMessage")
        fields = urllib.parse.parse_qs(data.decode())
        self.assertEqual(fields["chat_id"], ["42"])
        self.assertIn("@test_bot", fields["text"][0])
        self.assertIn("Sesión caducada", fields["text"][0])
        self.assertIn("renueva las cookies", fields["text"][0])

    def test_failure_is_reported_not_raised(self):
        settings = make_settings(
            self.tmp,
            RADIOBOT_TELEGRAM_BOT_TOKEN="123:ABC",
            RADIOBOT_TELEGRAM_CHAT_ID="42",
        )
        with mock.patch.object(notify, "_request", side_effect=OSError("sin red")):
            self.assertFalse(Notifier(settings).send("x", "y"))

    def test_token_not_in_repr(self):
        settings = make_settings(
            self.tmp,
            RADIOBOT_TELEGRAM_BOT_TOKEN="123:SECRETO",
            RADIOBOT_TELEGRAM_CHAT_ID="42",
        )
        self.assertNotIn("SECRETO", repr(settings))

    def test_supergroup_topic(self):
        settings = make_settings(
            self.tmp,
            RADIOBOT_TELEGRAM_BOT_TOKEN="123:ABC",
            RADIOBOT_TELEGRAM_CHAT_ID="-1001234567890",
            RADIOBOT_TELEGRAM_THREAD_ID="55",
        )
        with mock.patch.object(notify, "_request") as req:
            Notifier(settings).send("x", "y")
        fields = urllib.parse.parse_qs(req.call_args.args[1].decode())
        self.assertEqual(fields["chat_id"], ["-1001234567890"])
        self.assertEqual(fields["message_thread_id"], ["55"])

    def test_no_thread_means_general(self):
        settings = make_settings(
            self.tmp,
            RADIOBOT_TELEGRAM_BOT_TOKEN="123:ABC",
            RADIOBOT_TELEGRAM_CHAT_ID="-1001234567890",
        )
        with mock.patch.object(notify, "_request") as req:
            Notifier(settings).send("x", "y")
        self.assertNotIn("message_thread_id", req.call_args.args[1].decode())

    def test_invalid_chat_id(self):
        for bad in ("100123abc", "t.me/c/123", "-100 123"):
            with self.assertRaises(ConfigError, msg=bad):
                make_settings(
                    self.tmp,
                    RADIOBOT_TELEGRAM_BOT_TOKEN="123:ABC",
                    RADIOBOT_TELEGRAM_CHAT_ID=bad,
                )

    def test_hint_for_migrated_group(self):
        body = (
            b'{"ok":false,"error_code":400,"description":"Bad Request: group chat was upgraded'
            b' to a supergroup chat","parameters":{"migrate_to_chat_id":-1009876543210}}'
        )
        err = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(body))
        self.assertIn("-1009876543210", notify._telegram_hint(err))

    def test_hint_for_missing_topic(self):
        body = b'{"ok":false,"error_code":400,"description":"Bad Request: message thread not found"}'
        err = urllib.error.HTTPError("u", 400, "Bad Request", {}, io.BytesIO(body))
        self.assertIn("THREAD_ID", notify._telegram_hint(err))

    def test_requires_both_values(self):
        with self.assertRaises(ConfigError):
            make_settings(self.tmp, RADIOBOT_TELEGRAM_BOT_TOKEN="123:ABC")


if __name__ == "__main__":
    unittest.main()
