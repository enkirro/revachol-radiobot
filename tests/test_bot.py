import asyncio
import random
import unittest
from pathlib import Path

from helpers import make_settings, tmpdir

from revachol_radiobot.bot import Bot, ExitCode
from revachol_radiobot.dataset import load_entries
from revachol_radiobot.notify import Notifier
from revachol_radiobot.publisher import AuthError, Duplicate, PublisherError, RateLimited, TransientError
from revachol_radiobot.state import State


class FakePublisher:
    """Publicador de pruebas: ``script`` es una lista de excepciones o None (éxito)."""

    def __init__(self, script=None, auth_error=None):
        self.script = list(script or [])
        self.auth_error = auth_error
        self.posted = []
        self.persisted = 0
        self._n = 0

    async def check_auth(self):
        if self.auth_error:
            raise self.auth_error
        return "ok"

    async def post(self, text, reply_to=None):
        outcome = self.script.pop(0) if self.script else None
        if isinstance(outcome, Exception):
            raise outcome
        self._n += 1
        tid = str(1000 + self._n)
        self.posted.append((tid, text, reply_to))
        return tid

    def persist_cookies(self):
        self.persisted += 1

    async def close(self):
        pass


class RecordingNotifier(Notifier):
    def __init__(self, settings):
        super().__init__(settings)
        self.sent = []
        self.bodies = []

    def send(self, subject, body, *, ok=False):
        self.sent.append(subject)
        self.bodies.append(body)
        return True


async def _no_sleep(_):
    return None


class BotTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tmpdir()
        self.tmp = Path(self._tmp.name)
        self.settings = make_settings(self.tmp)
        self.entries = load_entries(self.settings.dataset_path)
        self.state = State(self.settings.state_db)

    def tearDown(self):
        self.state.close()
        self._tmp.cleanup()

    def run_bot(self, publisher, settings=None, seed=0):
        settings = settings or self.settings
        notifier = RecordingNotifier(settings)
        bot = Bot(
            settings, self.state, self.entries, publisher, notifier,
            rng=random.Random(seed), sleep=_no_sleep,
        )
        return asyncio.run(bot.run_once()), notifier

    def test_dataset_dedup_and_filters_empty(self):
        # 6 registros: 1 duplicado y 1 vacío -> 4 entradas
        self.assertEqual(len(self.entries), 4)

    def test_posts_and_never_repeats_within_cycle(self):
        texts = set()
        for seed in range(3):
            pub = FakePublisher()
            result, _ = self.run_bot(pub, seed=seed)
            self.assertEqual(result.code, ExitCode.OK)
            texts.add(pub.posted[0][1])
            self.assertEqual(pub.persisted, 1)
        self.assertEqual(len(texts), 3)
        stats = self.state.stats(len(self.entries))
        # La entrada larguísima necesita > 2 tuits: se omite, no se publica.
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(stats.posted_all_time, 3)

    def test_recycles_when_exhausted(self):
        for seed in range(3):
            self.run_bot(FakePublisher(), seed=seed)
        result, _ = self.run_bot(FakePublisher(), seed=9)
        self.assertEqual(result.code, ExitCode.OK)
        self.assertEqual(self.state.cycle, 2)

    def test_no_recycle_reports_nothing_to_post(self):
        settings = make_settings(self.tmp, RADIOBOT_RECYCLE="false")
        for seed in range(3):
            self.run_bot(FakePublisher(), settings, seed)
        result, notifier = self.run_bot(FakePublisher(), settings)
        self.assertEqual(result.code, ExitCode.NOTHING_TO_POST)
        self.assertEqual(len(notifier.sent), 1)

    def test_long_entry_becomes_thread(self):
        long_text = " ".join(["Frase de prueba."] * 25)
        settings = make_settings(self.tmp, [{"Languages": "Kim", "Translation": long_text}])
        self.entries = load_entries(settings.dataset_path)
        pub = FakePublisher()
        result, _ = self.run_bot(pub, settings)
        self.assertEqual(result.code, ExitCode.OK)
        self.assertEqual(len(pub.posted), 2)
        # Cada parte responde a la anterior.
        for (prev_id, _, _), (_, _, reply_to) in zip(pub.posted, pub.posted[1:], strict=False):
            self.assertEqual(reply_to, prev_id)
        self.assertTrue(pub.posted[0][1].endswith(f"(1/{len(pub.posted)})"))

    def test_auth_error_notifies_once_with_cooldown(self):
        pub = FakePublisher(auth_error=AuthError("caducadas"))
        r1, n1 = self.run_bot(pub)
        r2, n2 = self.run_bot(pub)
        self.assertEqual(r1.code, ExitCode.AUTH)
        self.assertEqual(r2.code, ExitCode.AUTH)
        self.assertEqual(len(n1.sent), 1)
        self.assertEqual(len(n2.sent), 0)  # enfriamiento
        self.assertEqual(n1.sent, ["X no acepta la sesión del bot"])
        self.assertIn("cookies set", n1.bodies[0])
        # Al recuperar la sesión, el aviso se rearma.
        self.run_bot(FakePublisher())
        self.assertIsNone(self.state.get_meta("notified:auth"))

    def test_locked_account_says_to_solve_captcha_first(self):
        pub = FakePublisher(auth_error=AuthError("captcha", AuthError.LOCKED))
        _, notifier = self.run_bot(pub)
        body = notifier.bodies[0]
        self.assertIn("resuelve la verificación", body)
        self.assertIn("revachol-radiobot test_bot check", body)
        self.assertIn("Solo si check sigue fallando", body)

    def test_suspended_account_does_not_suggest_cookies(self):
        pub = FakePublisher(auth_error=AuthError("suspendida", AuthError.SUSPENDED))
        _, notifier = self.run_bot(pub)
        self.assertNotIn("cookies set", notifier.bodies[0])

    def test_auth_error_while_posting(self):
        _, notifier = self.run_bot(FakePublisher([AuthError("401")]))
        self.assertEqual(notifier.sent, ["X ha rechazado la sesión al publicar"])

    def test_duplicate_picks_another_entry(self):
        pub = FakePublisher([Duplicate("dup")])
        result, _ = self.run_bot(pub)
        self.assertEqual(result.code, ExitCode.OK)
        self.assertEqual(self.state.stats(len(self.entries)).rejected, 1)

    def test_transient_errors_are_retried(self):
        pub = FakePublisher([TransientError("timeout"), TransientError("503")])
        result, _ = self.run_bot(pub)
        self.assertEqual(result.code, ExitCode.OK)

    def test_duplicate_after_retry_counts_as_posted(self):
        pub = FakePublisher([TransientError("timeout"), Duplicate("dup")])
        result, _ = self.run_bot(pub)
        self.assertEqual(result.code, ExitCode.OK)
        self.assertEqual(self.state.stats(len(self.entries)).rejected, 0)

    def test_rate_limit_is_tempfail_and_notified_once(self):
        result, notifier = self.run_bot(FakePublisher([RateLimited("429")]))
        self.assertEqual(result.code, ExitCode.TEMPFAIL)
        self.assertEqual(notifier.sent, ["X ha limitado la cuenta"])
        self.assertEqual(self.state.stats(len(self.entries)).posted_all_time, 0)
        _, notifier2 = self.run_bot(FakePublisher([RateLimited("429")]))
        self.assertEqual(notifier2.sent, [])  # enfriamiento

    def test_recovery_is_notified_after_a_failure(self):
        self.run_bot(FakePublisher(auth_error=AuthError("caducadas")))
        result, notifier = self.run_bot(FakePublisher())
        self.assertEqual(result.code, ExitCode.OK)
        self.assertEqual(notifier.sent, ["Vuelve a publicar con normalidad"])
        # Siguiente publicación normal: sin avisos.
        _, notifier = self.run_bot(FakePublisher(), seed=1)
        self.assertEqual(notifier.sent, [])

    def test_all_attempts_rejected_is_notified(self):
        pub = FakePublisher([Duplicate("d"), Duplicate("d"), Duplicate("d")])
        result, notifier = self.run_bot(pub)
        self.assertEqual(result.code, ExitCode.ERROR)
        self.assertEqual(notifier.sent, ["No se pudo publicar"])

    def test_partial_thread_is_recorded(self):
        long_text = " ".join(["Frase de prueba."] * 25)
        settings = make_settings(self.tmp, [{"Languages": "Kim", "Translation": long_text}])
        self.entries = load_entries(settings.dataset_path)
        pub = FakePublisher([None, PublisherError("boom")])
        result, notifier = self.run_bot(pub, settings)
        self.assertEqual(result.code, ExitCode.ERROR)
        self.assertEqual(len(result.tweet_ids), 1)
        self.assertEqual(len(notifier.sent), 1)
        # No se vuelve a intentar en este ciclo (evita hilos duplicados).
        self.assertEqual(self.state.stats(1).remaining_this_cycle, 0)


if __name__ == "__main__":
    unittest.main()


class InitFailureTests(unittest.TestCase):
    """Si no se puede crear el cliente (p. ej. sin cookies) se avisa respetando el enfriamiento."""

    def test_missing_cookies_notifies_once(self):
        from unittest import mock

        from revachol_radiobot import cli

        with tmpdir() as d:
            tmp = Path(d)
            env = {
                "RADIOBOT_HOME": str(tmp),
                "RADIOBOT_ACCOUNT": "test_bot",
                "RADIOBOT_TELEGRAM_BOT_TOKEN": "1:A",
                "RADIOBOT_TELEGRAM_CHAT_ID": "-1001",
            }
            make_settings(tmp)  # escribe el dataset de ejemplo
            sent = []

            def fake_publisher(*a, **k):
                raise AuthError("No existe el fichero de cookies")

            with mock.patch.dict("os.environ", env), \
                    mock.patch.object(cli, "TwikitPublisher", fake_publisher), \
                    mock.patch.object(Notifier, "send", lambda self, s, b, **k: sent.append(b)):
                self.assertEqual(cli.main(["post"]), ExitCode.AUTH)
                self.assertEqual(cli.main(["post"]), ExitCode.AUTH)
            self.assertEqual(len(sent), 1)
            self.assertIn("cookies set", sent[0])
