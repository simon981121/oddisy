import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timezone
from unittest import mock

import requests

import clv
import logger
from calculator import calculate_fair_odds
from test_api import KEY, LEAKY_URL, import_api

NOW = datetime(2026, 10, 7, 18, 0, tzinfo=timezone.utc)
UNIBET = {"key": "unibet_se", "title": "Unibet"}

H2H = {"key": "h2h", "outcomes": [
    {"name": "Hemma", "price": 2.00},
    {"name": "Borta", "price": 3.60},
    {"name": "Draw", "price": 4.00},
]}


def bet(market_key="h2h", outcome="Hemma", point=None, offered_odds=2.20):
    return {"market_key": market_key, "outcome_name": outcome, "point": point,
            "offered_odds": offered_odds}


def totals(*lines):
    """Pinnacle totals-marknad med ett Over/Under-par per (point, over_price, under_price)."""
    outcomes = []
    for point, over, under in lines:
        outcomes += [{"name": "Over", "price": over, "point": point},
                     {"name": "Under", "price": under, "point": point}]
    return {"key": "totals", "outcomes": outcomes}


class TestComputeClv(unittest.TestCase):
    def test_h2h_positive_clv(self):
        fair, value, status = clv.compute_clv(bet(offered_odds=2.20), H2H)
        expected_fair = calculate_fair_odds(H2H["outcomes"])[0]
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(fair, expected_fair)
        self.assertAlmostEqual(value, 2.20 / expected_fair - 1)
        self.assertGreater(value, 0)

    def test_h2h_negative_clv(self):
        fair, value, status = clv.compute_clv(bet(offered_odds=1.90), H2H)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(value, 1.90 / fair - 1)
        self.assertLess(value, 0)

    def test_h2h_picks_right_outcome(self):
        fair, _, status = clv.compute_clv(bet(outcome="Draw", offered_odds=4.5), H2H)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(fair, calculate_fair_odds(H2H["outcomes"])[2])

    def test_totals_same_line(self):
        market = totals((2.5, 1.95, 1.95))
        fair, value, status = clv.compute_clv(bet("totals", "Over", 2.5, 2.05), market)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(fair, 2.0, places=6)        # symmetriskt par -> 2.0
        self.assertAlmostEqual(value, 0.025, places=6)

    def test_totals_uses_only_bets_line(self):
        market = totals((2.5, 1.80, 2.10), (3.0, 2.20, 1.70))
        fair, _, status = clv.compute_clv(bet("totals", "Under", 2.5, 2.30), market)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(fair, calculate_fair_odds(market["outcomes"][:2])[1])

    def test_totals_line_changed(self):
        market = totals((3.0, 1.95, 1.95))
        self.assertEqual(clv.compute_clv(bet("totals", "Over", 2.5, 2.05), market),
                         (None, None, "line_changed"))

    def test_missing_outcome(self):
        two_way = {"key": "h2h", "outcomes": H2H["outcomes"][:2]}
        self.assertEqual(clv.compute_clv(bet(outcome="Draw"), two_way), (None, None, "missing"))

    def test_missing_market(self):
        self.assertEqual(clv.compute_clv(bet(), None), (None, None, "missing"))
        self.assertEqual(clv.compute_clv(bet(), {"key": "h2h", "outcomes": []}),
                         (None, None, "missing"))
        self.assertEqual(clv.compute_clv(bet("totals", "Over", 2.5), H2H),
                         (None, None, "missing"))

    def test_single_outcome_or_bad_price_is_missing(self):
        lonely = {"key": "h2h", "outcomes": [{"name": "Hemma", "price": 2.0}]}
        self.assertEqual(clv.compute_clv(bet(), lonely), (None, None, "missing"))
        broken = {"key": "h2h", "outcomes": [{"name": "Hemma", "price": 0},
                                             {"name": "Borta", "price": 1.5}]}
        self.assertEqual(clv.compute_clv(bet(), broken), (None, None, "missing"))


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = logger.DB_FILE
        self.db = os.path.join(self.tmp.name, "test_bets.db")
        logger.DB_FILE = self.db
        logger.init_db()
        self.conn = sqlite3.connect(self.db)

    def tearDown(self):
        self.conn.close()
        logger.DB_FILE = self.original_db
        self.tmp.cleanup()

    def add(self, match_id, commence_time, market_key="h2h", outcome="Hemma", point=""):
        match = {"id": match_id, "commence_time": commence_time,
                 "home_team": "Hemma", "away_team": "Borta"}
        logger.log_bet(match, "soccer_sweden_allsvenskan", market_key, outcome, point,
                       UNIBET, 2.1, 1.95, 0.08, 3.5)
        return self.conn.execute("SELECT MAX(id) FROM bets").fetchone()[0]

    def row(self, bet_id):
        return self.conn.execute("SELECT closing_fair, clv, clv_status FROM bets WHERE id = ?",
                                 (bet_id,)).fetchone()


class TestSelectBets(DbTestCase):
    def test_only_window(self):
        self.add("started", "2026-10-07T17:59:00Z")       # startade för 1 min sedan
        at_now = self.add("now", "2026-10-07T18:00:00Z")
        inside = self.add("inside", "2026-10-07T18:10:00Z")
        edge = self.add("edge", "2026-10-07T18:15:00Z")
        self.add("late", "2026-10-07T18:16:00Z")          # 16 min framåt
        self.add("tomorrow", "2026-10-08T18:05:00Z")
        ids = [b["id"] for b in clv.select_bets(self.conn, NOW)]
        self.assertEqual(ids, [at_now, inside, edge])

    def test_handled_bets_excluded(self):
        done = self.add("done", "2026-10-07T18:05:00Z")
        open_ = self.add("open", "2026-10-07T18:05:00Z", outcome="Borta")
        clv.save_clv(self.conn, done, None, None, "missing")
        self.assertEqual([b["id"] for b in clv.select_bets(self.conn, NOW)], [open_])

    def test_rows_by_name(self):
        self.add("m1", "2026-10-07T18:05:00Z", market_key="totals", outcome="Over", point=2.5)
        b = clv.select_bets(self.conn, NOW)[0]
        self.assertEqual((b["market_key"], b["outcome_name"], b["point"]), ("totals", "Over", 2.5))


class TestSaveClv(DbTestCase):
    def test_saves_values(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z")
        self.assertTrue(clv.save_clv(self.conn, bet_id, 2.0, 0.05, "ok"))
        self.assertEqual(self.row(bet_id), (2.0, 0.05, "ok"))

    def test_handled_bet_not_overwritten(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z")
        clv.save_clv(self.conn, bet_id, 2.0, 0.05, "ok")
        self.assertFalse(clv.save_clv(self.conn, bet_id, 2.5, -0.16, "ok"))
        self.assertFalse(clv.save_clv(self.conn, bet_id, None, None, "missing"))
        self.assertEqual(self.row(bet_id), (2.0, 0.05, "ok"))

    def test_line_changed_saved_without_values(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z", market_key="totals", outcome="Over", point=2.5)
        self.assertTrue(clv.save_clv(self.conn, bet_id, None, None, "line_changed"))
        self.assertEqual(self.row(bet_id), (None, None, "line_changed"))

    def test_invalid_status(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z")
        with self.assertRaises(ValueError):
            clv.save_clv(self.conn, bet_id, 2.0, 0.05, "klar")
        self.assertEqual(self.row(bet_id), (None, None, None))

    def test_unknown_id(self):
        self.assertFalse(clv.save_clv(self.conn, 99, 2.0, 0.05, "ok"))


class TestMigration(unittest.TestCase):
    """En databas skapad före clv_status ska få kolumnen utan att befintliga rader ändras."""

    OLD_SCHEMA = """
        CREATE TABLE bets (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_date   TEXT NOT NULL,
            match_date    TEXT,
            commence_time TEXT,
            sport_key     TEXT,
            match_id      TEXT NOT NULL,
            match_name    TEXT,
            market_key    TEXT NOT NULL,
            outcome_name  TEXT NOT NULL,
            point         REAL,
            bookmaker     TEXT NOT NULL,
            offered_odds  REAL,
            fair_at_flag  REAL,
            edge          REAL,
            units         REAL,
            stake_flat    REAL DEFAULT 20,
            stake_kelly   REAL,
            result        TEXT CHECK (result IN ('W', 'L', 'P') OR result IS NULL),
            closing_fair  REAL,
            clv           REAL
        )
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = logger.DB_FILE
        logger.DB_FILE = os.path.join(self.tmp.name, "old_bets.db")
        with closing(sqlite3.connect(logger.DB_FILE)) as conn, conn:
            conn.execute(self.OLD_SCHEMA)
            conn.execute("""
                INSERT INTO bets (logged_date, commence_time, match_id, market_key, outcome_name,
                                  point, bookmaker, offered_odds, result, closing_fair, clv)
                VALUES ('2026-10-01T10:00:00+00:00', '2026-10-01T18:00:00Z', 'gammal', 'totals',
                        'Over', 2.5, 'unibet_se', 2.1, 'W', 1.98, 0.06)
            """)

    def tearDown(self):
        logger.DB_FILE = self.original_db
        self.tmp.cleanup()

    def test_adds_column_and_keeps_rows(self):
        with closing(sqlite3.connect(logger.DB_FILE)) as conn:
            before = conn.execute("SELECT * FROM bets").fetchall()
        logger.init_db()
        logger.init_db()                                  # andra körningen ska inte göra något
        with closing(sqlite3.connect(logger.DB_FILE)) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(bets)")]
            after = conn.execute("SELECT * FROM bets").fetchall()
        self.assertEqual(cols[-1], "clv_status")
        self.assertEqual(cols.count("clv_status"), 1)
        self.assertEqual(after, [before[0] + (None,)])

    def test_invalid_status_rejected_by_db(self):
        logger.init_db()
        with closing(sqlite3.connect(logger.DB_FILE)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE bets SET clv_status = 'klar'")

    def test_run_migrates_given_db(self):
        other = os.path.join(self.tmp.name, "annan.db")
        logger.DB_FILE = other                            # run ska använda sin egen sökväg
        old = os.path.join(self.tmp.name, "old_bets.db")
        with redirect_stdout(io.StringIO()):
            clv.run(old, fetch=lambda *a: self.fail("inget anrop väntat"), now=NOW)
        with closing(sqlite3.connect(old)) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(bets)")]
        self.assertIn("clv_status", cols)
        self.assertFalse(os.path.exists(other))


def event(*markets):
    """Påhittat svar från event-odds-endpointen med bara Pinnacle."""
    return {"id": "x", "bookmakers": [{"key": "pinnacle", "title": "Pinnacle", "markets": list(markets)}]}


class TestRun(DbTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []

    def fake_fetch(self, responses):
        """responses: {match_id: data}. Kostnad = antal begärda marknader, som i dokumentationen."""
        def fetch(sport_key, event_id, market_key):
            self.calls.append((sport_key, event_id, market_key))
            self.remaining = getattr(self, "remaining", 500) - len(market_key.split(","))
            return responses[event_id], str(len(market_key.split(","))), str(self.remaining)
        return fetch

    def run_clv(self, responses):
        with redirect_stdout(io.StringIO()) as out:
            summary = clv.run(self.db, self.fake_fetch(responses), NOW)
        return summary, out.getvalue()

    def test_one_call_per_match_and_costs_printed(self):
        self.add("m1", "2026-10-07T18:05:00Z")
        self.add("m1", "2026-10-07T18:05:00Z", market_key="totals", outcome="Over", point=2.5)
        self.add("m1", "2026-10-07T18:05:00Z", outcome="Borta")
        self.add("m2", "2026-10-07T18:10:00Z")
        market = event(H2H, totals((2.5, 1.95, 1.95)))
        _, out = self.run_clv({"m1": market, "m2": event(H2H)})
        self.assertEqual(self.calls, [("soccer_sweden_allsvenskan", "m1", "h2h,totals"),
                                      ("soccer_sweden_allsvenskan", "m2", "h2h")])
        self.assertIn("4 bets startar inom 15 min", out)
        self.assertIn("beräknad kostnad högst 3 krediter", out)
        self.assertIn("kostnad 2, kvar 498", out)
        self.assertIn("kostnad 1, kvar 497", out)

    def test_statuses_saved_and_summary(self):
        ok = self.add("m1", "2026-10-07T18:05:00Z")
        changed = self.add("m1", "2026-10-07T18:05:00Z", market_key="totals", outcome="Over", point=2.5)
        gone = self.add("m2", "2026-10-07T18:05:00Z", outcome="Draw")
        two_way = {"key": "h2h", "outcomes": H2H["outcomes"][:2]}
        summary, out = self.run_clv({"m1": event(H2H, totals((3.0, 1.95, 1.95))),
                                     "m2": event(two_way)})
        expected_fair = calculate_fair_odds(H2H["outcomes"])[0]
        fair, value, status = self.row(ok)
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(fair, expected_fair)
        self.assertAlmostEqual(value, 2.1 / expected_fair - 1)
        self.assertEqual(self.row(changed), (None, None, "line_changed"))
        self.assertEqual(self.row(gone), (None, None, "missing"))
        self.assertEqual((summary["ok"], summary["line_changed"], summary["missing"]),
                         ([ok], [changed], [gone]))
        self.assertAlmostEqual(summary["average_clv"], value)
        self.assertIn("Sammanfattning: 1 ok, 1 line_changed, 1 missing", out)
        self.assertIn(f"Snitt-CLV (ok): {value * 100:+.2f}%", out)

    def test_api_error_left_null_without_crash(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z")
        totals_id = self.add("m1", "2026-10-07T18:05:00Z", market_key="totals", outcome="Over", point=2.5)
        quota = ({"message": "Usage quota has been reached"}, None, None)
        with redirect_stdout(io.StringIO()) as out:
            summary = clv.run(self.db, lambda *a: quota, NOW)
        self.assertEqual(self.row(bet_id), (None, None, None))
        self.assertEqual(self.row(totals_id), (None, None, None))
        self.assertEqual(summary["retry"], [bet_id, totals_id])
        self.assertEqual(summary["missing"], [])
        self.assertIsNone(summary["average_clv"])
        self.assertIn("Usage quota has been reached", out.getvalue())
        self.assertIn("Sammanfattning: 0 ok, 0 line_changed, 0 missing", out.getvalue())
        self.assertIn("API-fel: 2 bets hoppade över, försöker igen nästa körning", out.getvalue())

    def test_network_and_http_errors_left_null(self):
        for data in ({"message": "nätverksfel (ConnectionError)"},
                     {"message": "ogiltigt svar (HTTP 502)"},
                     {"message": "Event not found"},
                     None, "trasigt"):
            with self.subTest(data=data):
                bet_id = self.add(f"m-{data!r}", "2026-10-07T18:05:00Z")
                with redirect_stdout(io.StringIO()):
                    summary = clv.run(self.db, lambda *a: (data, None, None), NOW)
                self.assertEqual(self.row(bet_id), (None, None, None))
                self.assertIn(bet_id, summary["retry"])

    def test_retried_next_run_after_api_error(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z")
        with redirect_stdout(io.StringIO()):
            clv.run(self.db, lambda *a: ({"message": "nätverksfel (Timeout)"}, None, None), NOW)
        self.assertIsNone(self.row(bet_id)[2])
        summary, _ = self.run_clv({"m1": event(H2H)})
        self.assertEqual(self.calls, [("soccer_sweden_allsvenskan", "m1", "h2h")])
        self.assertEqual(self.row(bet_id)[2], "ok")
        self.assertEqual(summary["retry"], [])

    def test_api_error_for_one_match_does_not_stop_others(self):
        failed = self.add("m1", "2026-10-07T18:05:00Z")
        ok = self.add("m2", "2026-10-07T18:05:00Z")
        responses = {"m1": {"message": "Usage quota has been reached"}, "m2": event(H2H)}
        summary, out = self.run_clv(responses)
        self.assertEqual(self.row(failed), (None, None, None))
        self.assertEqual(self.row(ok)[2], "ok")
        self.assertEqual((summary["ok"], summary["retry"]), ([ok], [failed]))
        self.assertIn("API-fel: 1 bets hoppade över", out)

    def test_no_pinnacle_in_response_is_missing(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z")
        summary, out = self.run_clv({"m1": {"id": "m1", "bookmakers": []}})
        self.assertEqual(self.row(bet_id), (None, None, "missing"))
        self.assertEqual(summary["retry"], [])
        self.assertNotIn("Varning", out)
        self.assertNotIn("API-fel", out)

    def test_nothing_in_window_no_calls(self):
        self.add("m1", "2026-10-07T19:00:00Z")
        summary, out = self.run_clv({})
        self.assertEqual(self.calls, [])
        self.assertIn("beräknad kostnad högst 0 krediter", out)
        self.assertIn("Snitt-CLV (ok): –", out)

    def test_handled_bets_not_fetched_again(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z")
        clv.save_clv(self.conn, bet_id, 2.0, 0.05, "ok")
        self.run_clv({})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.row(bet_id), (2.0, 0.05, "ok"))

    def test_missing_db_not_created(self):
        missing = os.path.join(self.tmp.name, "finns_inte.db")
        with self.assertRaises(FileNotFoundError):
            clv.run(missing, self.fake_fetch({}), NOW)
        self.assertFalse(os.path.exists(missing))


class TestRunWithApi(DbTestCase):
    """clv.run mot riktiga get_pinnacle_odds med mockad requests.get."""

    @classmethod
    def setUpClass(cls):
        cls.api = import_api()

    def test_timeout_left_null_not_missing(self):
        bet_id = self.add("m1", "2026-10-07T18:05:00Z")
        other = self.add("m2", "2026-10-07T18:10:00Z")
        errors = [requests.exceptions.ReadTimeout(LEAKY_URL), requests.exceptions.ConnectionError(LEAKY_URL)]
        with mock.patch.object(self.api, "API_KEY", KEY), \
                mock.patch.object(self.api.requests, "get", side_effect=errors) as get, \
                redirect_stdout(io.StringIO()) as out:
            summary = clv.run(self.db, self.api.get_pinnacle_odds, NOW)
        self.assertEqual(get.call_args.kwargs["timeout"], (5, 30))
        self.assertEqual(self.row(bet_id), (None, None, None))
        self.assertEqual(self.row(other), (None, None, None))
        self.assertEqual(summary["missing"], [])
        self.assertEqual(summary["retry"], [bet_id, other])
        self.assertIn("nätverksfel (ReadTimeout)", out.getvalue())
        self.assertIn("nätverksfel (ConnectionError)", out.getvalue())
        self.assertNotIn(KEY, out.getvalue())


if __name__ == "__main__":
    unittest.main()
