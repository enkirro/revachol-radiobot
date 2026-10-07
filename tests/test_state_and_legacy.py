import unittest
from pathlib import Path

from helpers import make_settings, tmpdir

from revachol_radiobot.config import ConfigError, Settings
from revachol_radiobot.dataset import DatasetError, load_entries
from revachol_radiobot.legacy import match_log
from revachol_radiobot.state import State


class StateTests(unittest.TestCase):
    def test_snapshot_is_isolated(self):
        with tmpdir() as d:
            with State(Path(d) / "s.db") as st:
                st.record("k1", "t1", "posted", ["1"])
                with st.snapshot() as snap:
                    snap.record("k2", "t2", "posted", ["2"])
                    self.assertEqual(snap.excluded_keys(), {"k1", "k2"})
                self.assertEqual(st.excluded_keys(), {"k1"})

    def test_skipped_excluded_across_cycles(self):
        with tmpdir() as d, State(Path(d) / "s.db") as st:
            st.record("long", "x", "skipped")
            st.record("ok", "y", "posted", ["1"])
            st.start_new_cycle()
            self.assertEqual(st.excluded_keys(), {"long"})


class LegacyImportTests(unittest.TestCase):
    def test_matches_single_and_threaded_lines(self):
        with tmpdir() as d:
            tmp = Path(d)
            long_tr = "palabra " * 60
            data = [
                {"Languages": "Tú", "Translation": "\"Tiene sentido\"."},
                {"Languages": "Kim", "Translation": long_tr},
            ]
            settings = make_settings(tmp, data)
            entries = load_entries(settings.dataset_path)
            raw_long = f"Kim: {long_tr}"
            log = tmp / "cron_log.log"
            log.write_text(
                'Tweet ID: 1 - Tweeted: Tú: "Tiene sentido".\n'
                "Traceback (most recent call last):\n"
                f"Tweet ID: 2 - Tweeted (part 1): {raw_long[:280]}\n"
                f"Tweet ID: 3 - Tweeted (part 2): {raw_long[280:]}\n"
                "Tweet ID: 4 - Tweeted: algo que ya no está en el dataset\n",
                "utf-8",
            )
            matches, report = match_log(log, entries)
            self.assertEqual(report.lines, 3)
            self.assertEqual(report.matched, 2)
            self.assertEqual(report.unmatched, 1)
            with State(settings.state_db) as st:
                n = st.bulk_import(((m.entry.key, m.entry.text, m.tweet_id) for m in matches), cycle=1)
                self.assertEqual(n, 2)
                # Importar dos veces no duplica.
                n = st.bulk_import(((m.entry.key, m.entry.text, m.tweet_id) for m in matches), cycle=1)
                self.assertEqual(n, 0)


class ConfigTests(unittest.TestCase):
    def test_paths_derive_from_bot_home(self):
        home = "/opt/revachol-radiobot/bots/discoelysium_es"
        s = Settings.from_env({"RADIOBOT_HOME": home})
        self.assertEqual(s.account, "discoelysium_es")
        self.assertEqual(str(s.dataset_path), f"{home}/dataset.json")
        self.assertEqual(str(s.state_db), f"{home}/state.db")
        self.assertEqual(str(s.cookies_path), f"{home}/cookies.json")
        self.assertEqual(s.impersonate, "chrome124")
        self.assertEqual(s.max_thread_parts, 2)
        self.assertIsNone(s.telegram)

    def test_user_agent_matches_impersonated_browser(self):
        s = Settings.from_env({"RADIOBOT_HOME": "/tmp/x"})
        self.assertIn("Chrome/124.0.0.0", s.user_agent)
        self.assertNotIn("Version/17.5", s.user_agent)
        s = Settings.from_env({"RADIOBOT_HOME": "/tmp/x", "RADIOBOT_IMPERSONATE": ""})
        self.assertIsNone(s.user_agent)
        s = Settings.from_env({"RADIOBOT_HOME": "/tmp/x", "RADIOBOT_USER_AGENT": "UA propio"})
        self.assertEqual(s.user_agent, "UA propio")

    def test_account_can_be_explicit_and_strips_at(self):
        s = Settings.from_env({"RADIOBOT_HOME": "/tmp/x", "RADIOBOT_ACCOUNT": "@discoelysium_es"})
        self.assertEqual(s.account, "discoelysium_es")

    def test_invalid_account(self):
        with self.assertRaises(ConfigError):
            Settings.from_env({"RADIOBOT_HOME": "/tmp/x", "RADIOBOT_ACCOUNT": "no-vale"})

    def test_old_channels_are_ignored(self):
        s = Settings.from_env({"RADIOBOT_ACCOUNT": "a", "RADIOBOT_MAILGUN_API_KEY": "k"})
        self.assertFalse(hasattr(s, "mailgun"))

    def test_bad_int(self):
        with self.assertRaises(ConfigError):
            Settings.from_env({"RADIOBOT_ACCOUNT": "a", "RADIOBOT_MAX_THREAD_PARTS": "muchos"})

    def test_missing_dataset(self):
        with self.assertRaises(DatasetError):
            load_entries(Path("/no/existe.json"))


if __name__ == "__main__":
    unittest.main()
