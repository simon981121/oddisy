import importlib
import io
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import requests

from test_api import KEY, LEAKY_URL, non_json_response, response


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def value_match(match_id="m1"):
    """Match i morgon där Unibet 2.10 på Hemma slår Pinnacles rättvisa 2.00 (edge 5 %)."""
    now = datetime.now(timezone.utc)
    return {
        "id": match_id, "commence_time": iso(now + timedelta(days=1)),
        "home_team": "Hemma", "away_team": "Borta",
        "bookmakers": [
            {"key": "pinnacle", "title": "Pinnacle", "last_update": iso(now),
             "markets": [{"key": "h2h", "outcomes": [{"name": "Hemma", "price": 2.0},
                                                     {"name": "Borta", "price": 2.0}]}]},
            {"key": "unibet_se", "title": "Unibet (SE)", "last_update": iso(now),
             "markets": [{"key": "h2h", "outcomes": [{"name": "Hemma", "price": 2.10},
                                                     {"name": "Borta", "price": 1.80}]}]},
        ],
    }


def sport(key, active=True):
    return {"key": key, "active": active}


class TestMainLoop(unittest.TestCase):
    """main.run med mockad requests.get. Databas, seen_bets.json och .env rörs aldrig."""

    @classmethod
    def setUpClass(cls):
        for name in ("main", "api"):
            sys.modules.pop(name, None)
        with mock.patch("dotenv.load_dotenv"), redirect_stdout(io.StringIO()):
            cls.main = importlib.import_module("main")
        cls.api = sys.modules["api"]

    @classmethod
    def tearDownClass(cls):
        for name in ("main", "api"):
            sys.modules.pop(name, None)

    def setUp(self):
        self.seen = {}
        for target, kwargs in [(self.api, {"API_KEY": KEY}),
                               (self.main, {"init_db": mock.Mock(),
                                            "load_seen": mock.Mock(return_value=self.seen),
                                            "save_seen": mock.Mock(),
                                            "log_bet": mock.Mock()})]:
            patcher = mock.patch.multiple(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_main(self, routes):
        """routes: {url-slut: svar eller undantag}. Returnerar (utskrift, mock för requests.get)."""
        def fake_get(url, params=None, timeout=None):
            for suffix, answer in routes.items():
                if url.endswith(suffix):
                    if isinstance(answer, Exception):
                        raise answer
                    return answer
            self.fail(f"oväntat anrop {url}")
        with mock.patch.object(self.api.requests, "get", side_effect=fake_get) as get, \
                redirect_stdout(io.StringIO()) as out:
            self.counts = self.main.run()
        return out.getvalue(), get

    def test_failing_sports_skipped_and_loop_continues(self):
        out, get = self.run_main({
            "/sports": response([sport("a"), sport("b"), sport("c"), sport("d"), sport("e")]),
            "/sports/a/odds": requests.exceptions.ReadTimeout(LEAKY_URL),
            "/sports/b/odds": requests.exceptions.ConnectionError(LEAKY_URL),
            "/sports/c/odds": non_json_response(502),
            "/sports/d/odds": response({"message": "Usage quota has been reached"}, status=429),
            "/sports/e/odds": response([value_match()]),
        })
        self.assertIn("Varning: hoppar över a, nätverksfel (ReadTimeout)", out)
        self.assertIn("Varning: hoppar över b, nätverksfel (ConnectionError)", out)
        self.assertIn("Varning: hoppar över c, ogiltigt svar (HTTP 502)", out)
        self.assertIn("Varning: hoppar över d, Usage quota has been reached", out)
        self.assertEqual(get.call_count, 6)
        self.assertTrue(all(c.kwargs["timeout"] == (5, 30) for c in get.call_args_list))
        self.main.log_bet.assert_called_once()
        self.assertEqual(self.main.log_bet.call_args.args[1:4], ("e", "h2h", "Hemma"))
        self.main.save_seen.assert_called_once_with(self.seen)
        self.assertEqual(list(self.seen), ["m1_h2h_Hemma_unibet_se"])
        self.assertEqual(self.counts, {"ok": 1, "network": 2, "api": 2, "unexpected": 0})
        self.assertIn("Sammanfattning: 1 sporter skannade, hoppade över 2 pga nätverksfel, "
                      "0 pga oväntade fel, 2 pga andra API-fel.", out)
        self.assertNotIn(KEY, out)

    def test_sports_request_fails(self):
        out, get = self.run_main({"/sports": requests.exceptions.ReadTimeout(LEAKY_URL)})
        self.assertIn("Varning: kunde inte hämta sporter, nätverksfel (ReadTimeout)", out)
        self.assertEqual(get.call_count, 1)
        self.main.save_seen.assert_called_once_with(self.seen)
        self.assertIn("Sammanfattning: 0 sporter skannade", out)
        self.assertNotIn(KEY, out)

    def test_unexpected_error_in_one_sport_does_not_stop_run(self):
        broken = value_match("trasig")
        del broken["commence_time"]
        out, _ = self.run_main({
            "/sports": response([sport("a"), sport("b")]),
            "/sports/a/odds": response([broken]),
            "/sports/b/odds": response([value_match()]),
        })
        with open(self.main.__file__, encoding="utf-8") as f:
            line = next(i for i, text in enumerate(f, start=1)
                        if 'match["commence_time"].replace' in text)
        self.assertIn(f"Varning: hoppar över a, KeyError i main.py:{line}\n", out)
        self.assertNotIn("'commence_time'", out)              # felets text skrivs inte ut
        self.assertEqual(self.counts, {"ok": 1, "network": 0, "api": 0, "unexpected": 1})
        self.assertIn("1 sporter skannade, hoppade över 0 pga nätverksfel, 1 pga oväntade fel", out)
        self.main.log_bet.assert_called_once()
        self.main.save_seen.assert_called_once_with(self.seen)

    def test_inactive_sport_not_fetched(self):
        _, get = self.run_main({"/sports": response([sport("a", active=False)])})
        self.assertEqual(get.call_count, 1)
        self.main.save_seen.assert_called_once()


if __name__ == "__main__":
    unittest.main()
