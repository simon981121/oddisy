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


def sport(key, active=True, has_outrights=False):
    return {"key": key, "active": active, "has_outrights": has_outrights}


# Sportnycklar som select_sports väljer (tre fotbollsligor i allowlistan, ATP och WTA)
A, B, C, D, E = ("soccer_italy_serie_a", "soccer_sweden_allsvenskan", "tennis_atp_x",
                 "tennis_wta_x", "soccer_germany_bundesliga")


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
            "/sports": response([sport(A), sport(B), sport(C), sport(D), sport(E)]),
            f"/sports/{A}/odds": requests.exceptions.ReadTimeout(LEAKY_URL),
            f"/sports/{B}/odds": requests.exceptions.ConnectionError(LEAKY_URL),
            f"/sports/{C}/odds": non_json_response(502),
            f"/sports/{D}/odds": response({"message": "Usage quota has been reached"}, status=429),
            f"/sports/{E}/odds": response([value_match()]),
        })
        self.assertIn(f"Varning: hoppar över {A}, nätverksfel (ReadTimeout)", out)
        self.assertIn(f"Varning: hoppar över {B}, nätverksfel (ConnectionError)", out)
        self.assertIn(f"Varning: hoppar över {C}, ogiltigt svar (HTTP 502)", out)
        self.assertIn(f"Varning: hoppar över {D}, Usage quota has been reached", out)
        self.assertEqual(get.call_count, 6)
        self.assertTrue(all(c.kwargs["timeout"] == (5, 30) for c in get.call_args_list))
        self.main.log_bet.assert_called_once()
        self.assertEqual(self.main.log_bet.call_args.args[1:4], (E, "h2h", "Hemma"))
        self.main.save_seen.assert_called_once_with(self.seen)
        self.assertEqual(list(self.seen), ["m1_h2h_Hemma_unibet_se"])
        self.assertEqual(self.counts, {"ok": 1, "network": 2, "api": 2, "unexpected": 0})
        self.assertIn("Sammanfattning: 1 sporter skannade, hoppade över 2 pga nätverksfel, "
                      "0 pga oväntade fel, 2 pga andra API-fel. 0 sporter bortvalda.", out)
        self.assertIn("Valda sporter: 5 (3 fotboll, 2 tennis). "
                      "Beräknad kostnad: 5 × 1 marknad(er) × 1 region = 5 krediter.", out)
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
            "/sports": response([sport(A), sport(B)]),
            f"/sports/{A}/odds": response([broken]),
            f"/sports/{B}/odds": response([value_match()]),
        })
        with open(self.main.__file__, encoding="utf-8") as f:
            line = next(i for i, text in enumerate(f, start=1)
                        if 'match["commence_time"].replace' in text)
        self.assertIn(f"Varning: hoppar över {A}, KeyError i main.py:{line}\n", out)
        self.assertNotIn("'commence_time'", out)              # felets text skrivs inte ut
        self.assertEqual(self.counts, {"ok": 1, "network": 0, "api": 0, "unexpected": 1})
        self.assertIn("1 sporter skannade, hoppade över 0 pga nätverksfel, 1 pga oväntade fel", out)
        self.main.log_bet.assert_called_once()
        self.main.save_seen.assert_called_once_with(self.seen)

    def test_inactive_sport_not_fetched(self):
        _, get = self.run_main({"/sports": response([sport(A, active=False)])})
        self.assertEqual(get.call_count, 1)
        self.main.save_seen.assert_called_once()

    def test_deselected_sports_not_fetched(self):
        out, get = self.run_main({
            "/sports": response([sport("basketball_nba"), sport("soccer_epl"),
                                 sport("soccer_fifa_world_cup_winner", has_outrights=True), sport(C)]),
            f"/sports/{C}/odds": response([]),
        })
        self.assertEqual([c.args[0].rsplit("/v4", 1)[1] for c in get.call_args_list],
                         ["/sports", f"/sports/{C}/odds"])
        self.assertIn("Valda sporter: 1 (0 fotboll, 1 tennis). "
                      "Beräknad kostnad: 1 × 1 marknad(er) × 1 region = 1 krediter.", out)
        self.assertLess(out.index("Valda sporter"), out.index("Sammanfattning"))
        self.assertIn("1 sporter skannade", out)
        self.assertIn("3 sporter bortvalda.", out)


class TestSelectSports(unittest.TestCase):
    """select_sports med påhittad sportlista. Inga anrop görs."""

    @classmethod
    def setUpClass(cls):
        sys.modules.pop("api", None)
        sys.modules.pop("main", None)
        with mock.patch("dotenv.load_dotenv"), redirect_stdout(io.StringIO()):
            cls.main = importlib.import_module("main")

    @classmethod
    def tearDownClass(cls):
        for name in ("main", "api"):
            sys.modules.pop(name, None)

    def keys(self, sports):
        return [s["key"] for s in self.main.select_sports(sports)]

    def test_soccer_in_allowlist_included(self):
        self.assertEqual(self.keys([sport(A), sport(B)]), [A, B])

    def test_soccer_outside_allowlist_excluded(self):
        self.assertEqual(self.keys([sport("soccer_epl"), sport(A), sport("soccer_usa_mls")]), [A])

    def test_empty_allowlist_takes_all_soccer(self):
        with mock.patch.object(self.main, "SOCCER_ALLOWLIST", []):
            self.assertEqual(self.keys([sport("soccer_epl"), sport(A), sport("basketball_nba")]),
                             ["soccer_epl", A])

    def test_atp_and_wta_included(self):
        self.assertEqual(self.keys([sport("tennis_atp_us_open"), sport("tennis_wta_wimbledon")]),
                         ["tennis_atp_us_open", "tennis_wta_wimbledon"])

    def test_basketball_and_icehockey_excluded(self):
        self.assertEqual(self.keys([sport("basketball_nba"), sport("icehockey_sweden_hockey_league"),
                                    sport("icehockey_nhl")]), [])

    def test_winner_and_outrights_excluded(self):
        self.assertEqual(self.keys([sport("soccer_italy_serie_a_winner"),
                                    sport("tennis_atp_x_winner"),
                                    sport(A, has_outrights=True),
                                    sport("tennis_wta_x", has_outrights=True)]), [])
        with mock.patch.object(self.main, "SOCCER_ALLOWLIST", []):
            self.assertEqual(self.keys([sport("soccer_fifa_world_cup_winner")]), [])

    def test_inactive_excluded(self):
        self.assertEqual(self.keys([sport(A, active=False), sport("tennis_atp_x", active=False)]), [])

    def test_missing_has_outrights_treated_as_false(self):
        self.assertEqual(self.keys([{"key": A, "active": True}]), [A])


if __name__ == "__main__":
    unittest.main()
