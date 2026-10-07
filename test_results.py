import io
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone

import logger
import results
import set_result

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
UNIBET = {"key": "unibet_se", "title": "Unibet"}


def bet(market_key="h2h", outcome="Hemma", point=None, sport_key="soccer_sweden_allsvenskan"):
    return {"sport_key": sport_key, "market_key": market_key, "outcome_name": outcome, "point": point}


def game(home_score, away_score, completed=True, game_id="m1"):
    return {"id": game_id, "completed": completed, "home_team": "Hemma", "away_team": "Borta",
            "scores": [{"name": "Hemma", "score": str(home_score)},
                       {"name": "Borta", "score": str(away_score)}]}


class TestSettle(unittest.TestCase):
    def test_h2h_home_win(self):
        self.assertEqual(results.settle(bet(outcome="Hemma"), game(2, 1)), "W")
        self.assertEqual(results.settle(bet(outcome="Borta"), game(2, 1)), "L")
        self.assertEqual(results.settle(bet(outcome="Draw"), game(2, 1)), "L")

    def test_h2h_away_win(self):
        self.assertEqual(results.settle(bet(outcome="Borta"), game(0, 3)), "W")
        self.assertEqual(results.settle(bet(outcome="Hemma"), game(0, 3)), "L")

    def test_h2h_draw(self):
        self.assertEqual(results.settle(bet(outcome="Draw"), game(1, 1)), "W")
        self.assertEqual(results.settle(bet(outcome="Hemma"), game(1, 1)), "L")
        self.assertEqual(results.settle(bet(outcome="Borta"), game(1, 1)), "L")

    def test_h2h_unknown_name(self):
        self.assertIsNone(results.settle(bet(outcome="Annat lag"), game(2, 1)))

    def test_totals_over(self):
        self.assertEqual(results.settle(bet("totals", "Over", 2.5), game(2, 1)), "W")
        self.assertEqual(results.settle(bet("totals", "Under", 2.5), game(2, 1)), "L")

    def test_totals_under(self):
        self.assertEqual(results.settle(bet("totals", "Under", 2.5), game(1, 0)), "W")
        self.assertEqual(results.settle(bet("totals", "Over", 2.5), game(1, 0)), "L")

    def test_totals_push(self):
        self.assertEqual(results.settle(bet("totals", "Over", 3.0), game(2, 1)), "P")
        self.assertEqual(results.settle(bet("totals", "Under", 3.0), game(2, 1)), "P")

    def test_icehockey_untouched(self):
        hockey = "icehockey_sweden_hockey_league"
        self.assertIsNone(results.settle(bet(outcome="Hemma", sport_key=hockey), game(3, 1)))
        self.assertIsNone(results.settle(bet("totals", "Over", 5.5, sport_key=hockey), game(4, 3)))

    def test_not_completed_or_missing(self):
        self.assertIsNone(results.settle(bet(), game(2, 1, completed=False)))
        self.assertIsNone(results.settle(bet(), None))
        no_scores = game(0, 0)
        no_scores["scores"] = None
        self.assertIsNone(results.settle(bet(), no_scores))


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = logger.DB_FILE
        self.db = os.path.join(self.tmp.name, "test_bets.db")
        logger.DB_FILE = self.db
        logger.init_db()
        self.calls = []

    def tearDown(self):
        logger.DB_FILE = self.original_db
        self.tmp.cleanup()

    def add(self, match_id, commence_time, sport_key="soccer_sweden_allsvenskan",
            market_key="h2h", outcome="Hemma", point=""):
        match = {"id": match_id, "commence_time": commence_time,
                 "home_team": "Hemma", "away_team": "Borta"}
        logger.log_bet(match, sport_key, market_key, outcome, point, UNIBET, 2.1, 1.95, 0.08, 3.5)

    def fake_fetch(self, games):
        def fetch(sport_key, days_from, event_ids):
            self.calls.append((sport_key, days_from, event_ids))
            return games
        return fetch

    def run_results(self, games):
        with redirect_stdout(io.StringIO()) as out:
            summary = results.run(self.db, self.fake_fetch(games), NOW)
        return summary, out.getvalue()

    def result_of(self, match_id):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT result FROM bets WHERE match_id = ?", (match_id,)).fetchone()[0]

    def test_settles_and_writes_to_db(self):
        self.add("m1", "2026-10-06T18:00:00Z")
        summary, out = self.run_results([game(2, 1, game_id="m1")])
        self.assertEqual(self.result_of("m1"), "W")
        self.assertEqual(self.calls, [("soccer_sweden_allsvenskan", 3, ["m1"])])
        self.assertIn("beräknad kostnad 2 krediter", out)

    def test_too_recent_not_fetched(self):
        self.add("m1", "2026-10-07T10:00:00Z")          # 2 h sedan
        summary, _ = self.run_results([game(2, 1, game_id="m1")])
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.result_of("m1"))

    def test_icehockey_left_null_and_not_fetched(self):
        self.add("h1", "2026-10-06T18:00:00Z", sport_key="icehockey_sweden_hockey_league")
        summary, out = self.run_results([game(3, 1, game_id="h1")])
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.result_of("h1"))
        self.assertEqual(len(summary["manual"]), 1)
        self.assertIn("avgör manuellt", out)

    def test_not_completed_after_48h_is_unclear(self):
        self.add("old", "2026-10-05T10:00:00Z")         # 50 h sedan
        self.add("new", "2026-10-06T18:00:00Z")         # 18 h sedan
        summary, out = self.run_results([game(1, 0, completed=False, game_id="old"),
                                         game(1, 0, completed=False, game_id="new")])
        self.assertIsNone(self.result_of("old"))
        self.assertIsNone(self.result_of("new"))
        self.assertEqual(len(summary["unclear"]), 1)
        self.assertIn("Oklart", out)

    def test_one_call_per_sport(self):
        self.add("a1", "2026-10-06T18:00:00Z")
        self.add("a2", "2026-10-06T19:00:00Z", market_key="totals", outcome="Over", point=2.5)
        self.add("b1", "2026-10-06T18:00:00Z", sport_key="basketball_nba", outcome="Borta")
        self.run_results([])
        self.assertEqual(sorted(c[0] for c in self.calls),
                         ["basketball_nba", "soccer_sweden_allsvenskan"])
        self.assertEqual([c[2] for c in self.calls if c[0].startswith("soccer")], [["a1", "a2"]])

    def test_api_error_does_not_crash(self):
        self.add("m1", "2026-10-06T18:00:00Z")
        summary, out = self.run_results({"message": "Usage quota has been reached"})
        self.assertIsNone(self.result_of("m1"))
        self.assertIn("Varning", out)

    def test_already_settled_not_touched(self):
        self.add("m1", "2026-10-06T18:00:00Z")
        set_result.set_result(1, "L", self.db)
        self.run_results([game(2, 1, game_id="m1")])
        self.assertEqual(self.result_of("m1"), "L")
        self.assertEqual(self.calls, [])


class TestSetResult(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = logger.DB_FILE
        self.db = os.path.join(self.tmp.name, "test_bets.db")
        logger.DB_FILE = self.db
        logger.init_db()
        logger.log_bet({"id": "m1", "commence_time": "2026-10-06T18:00:00Z",
                        "home_team": "Hemma", "away_team": "Borta"},
                       "icehockey_sweden_hockey_league", "h2h", "Hemma", "", UNIBET,
                       2.1, 1.95, 0.08, 3.5)

    def tearDown(self):
        logger.DB_FILE = self.original_db
        self.tmp.cleanup()

    def result(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT result FROM bets WHERE id = 1").fetchone()[0]

    def test_sets_result_case_insensitive(self):
        set_result.set_result(1, "w", self.db)
        self.assertEqual(self.result(), "W")

    def test_invalid_result(self):
        with self.assertRaises(set_result.SetResultError):
            set_result.set_result(1, "X", self.db)
        self.assertIsNone(self.result())

    def test_missing_id(self):
        with self.assertRaisesRegex(set_result.SetResultError, "Inget bet"):
            set_result.set_result(99, "W", self.db)

    def test_no_overwrite_without_force(self):
        set_result.set_result(1, "W", self.db)
        with self.assertRaisesRegex(set_result.SetResultError, "--force"):
            set_result.set_result(1, "L", self.db)
        set_result.set_result(1, "L", self.db, force=True)
        self.assertEqual(self.result(), "L")

    def test_missing_db_not_created(self):
        missing = os.path.join(self.tmp.name, "finns_inte.db")
        with self.assertRaises(set_result.SetResultError):
            set_result.set_result(1, "W", missing)
        self.assertFalse(os.path.exists(missing))


if __name__ == "__main__":
    unittest.main()
