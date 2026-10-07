import asyncio
import unittest

from revachol_radiobot.publisher import (
    AuthError,
    DryRunPublisher,
    Duplicate,
    PublisherError,
    RateLimited,
    TransientError,
    TwikitPublisher,
)

try:
    import twikit
except ImportError:  # pragma: no cover
    twikit = None


class DryRunTests(unittest.TestCase):
    def test_returns_incremental_ids(self):
        pub = DryRunPublisher()
        ids = asyncio.run(_post_two(pub))
        self.assertEqual(ids, ["dry-1", "dry-2"])
        self.assertEqual(pub.posted[1], ("b", "dry-1"))


async def _post_two(pub):
    first = await pub.post("a")
    return [first, await pub.post("b", reply_to=first)]


@unittest.skipIf(twikit is None, "twifork no instalado")
class TwikitErrorMappingTests(unittest.TestCase):
    """Comprueba que las excepciones reales de twifork se traducen bien."""

    def setUp(self):
        self.pub = TwikitPublisher.__new__(TwikitPublisher)
        self.pub._tw = twikit

    def check(self, exc, expected):
        self.assertIsInstance(self.pub._translate(exc), expected)

    def test_mapping(self):
        self.check(twikit.InvalidSession("x"), AuthError)
        self.check(twikit.Unauthorized("x"), AuthError)
        self.check(twikit.AccountSuspended("x"), AuthError)
        self.check(twikit.TooManyRequests("x"), RateLimited)
        self.check(twikit.DuplicateTweet("x"), Duplicate)
        self.check(twikit.CouldNotTweet("You've hit the daily limit (501)"), RateLimited)
        self.check(twikit.CouldNotTweet("otra cosa"), PublisherError)
        self.check(twikit.ServerError("x"), TransientError)
        self.check(ConnectionResetError(), TransientError)
        self.check(ValueError("x"), PublisherError)


if __name__ == "__main__":
    unittest.main()


class _Exc:
    """Espacio de nombres con excepciones equivalentes a las de twifork."""

    class TwitterException(Exception):
        pass

    class Unauthorized(TwitterException):
        pass

    class Forbidden(TwitterException):
        pass

    class AccountLocked(TwitterException):
        pass

    class AccountSuspended(TwitterException):
        pass

    class ClientTransactionError(TwitterException):
        pass

    class InvalidSession(ClientTransactionError):
        pass

    class TooManyRequests(TwitterException):
        pass

    class DuplicateTweet(TwitterException):
        pass

    class CouldNotTweet(TwitterException):
        pass

    class ServerError(TwitterException):
        pass


class _FakeV11:
    def __init__(self, outcome):
        self.outcome = outcome

    async def settings(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome, None


class _FakeClient:
    def __init__(self, outcome, jar=None):
        self.v11 = _FakeV11(outcome)
        self.jar = jar or {}

    def get_cookies(self):
        return dict(self.jar)


def _publisher(outcome, account="discoelysium_es", cookies_path=None, jar=None):
    pub = TwikitPublisher.__new__(TwikitPublisher)
    pub._tw = _Exc
    pub._client = _FakeClient(outcome, jar)
    pub._account = account
    pub._cookies_path = cookies_path
    return pub


class CheckAuthTests(unittest.TestCase):
    def test_ok_returns_screen_name(self):
        pub = _publisher({"screen_name": "DiscoElysium_ES"})
        self.assertEqual(asyncio.run(pub.check_auth()), "DiscoElysium_ES")

    def test_cookies_from_another_account(self):
        pub = _publisher({"screen_name": "Enkirro"})
        with self.assertRaisesRegex(AuthError, "@Enkirro"):
            asyncio.run(pub.check_auth())

    def test_reasons_are_specific(self):
        cases = [
            (_Exc.AccountLocked("326"), "bloqueado"),
            (_Exc.AccountSuspended("suspended"), "suspendida"),
            (_Exc.Unauthorized('status: 401, message: "Could not authenticate you (32)"'), "401"),
            (_Exc.Forbidden('{"errors":[{"code":353}]}'), "ct0"),
            (_Exc.InvalidSession("shell"), "cerrada"),
        ]
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__):
                with self.assertRaisesRegex(AuthError, expected):
                    asyncio.run(_publisher(exc).check_auth())

    def test_non_dict_response(self):
        with self.assertRaises(AuthError):
            asyncio.run(_publisher("<html>cloudflare</html>").check_auth())

    def test_network_error_is_transient(self):
        with self.assertRaises(TransientError):
            asyncio.run(_publisher(ConnectionResetError()).check_auth())


class PersistCookiesTests(unittest.TestCase):
    def test_backup_and_skip_when_unchanged(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cookies.json"
            path.write_text(json.dumps({"auth_token": "A", "ct0": "old"}))
            pub = _publisher({}, cookies_path=path, jar={"auth_token": "A", "ct0": "new"})
            pub.persist_cookies()
            self.assertEqual(json.loads(path.read_text())["ct0"], "new")
            self.assertEqual(json.loads((Path(d) / "cookies.json.bak").read_text())["ct0"], "old")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            # Sin cambios: no se reescribe ni se toca la copia.
            mtime = path.stat().st_mtime_ns
            pub.persist_cookies()
            self.assertEqual(path.stat().st_mtime_ns, mtime)

    def test_never_writes_incomplete_jar(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cookies.json"
            path.write_text(json.dumps({"auth_token": "A", "ct0": "B"}))
            _publisher({}, cookies_path=path, jar={"ct0": "B"}).persist_cookies()
            self.assertEqual(json.loads(path.read_text())["auth_token"], "A")
