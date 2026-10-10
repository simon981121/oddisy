import os
import sqlite3
import tempfile
import unittest
from contextlib import closing

import logger


MATCH = {
    "id": "abc123",
    "commence_time": "2026-10-08T18:00:00Z",
    "home_team": "Lag A",
    "away_team": "Lag B",
}
UNIBET = {"key": "unibet_se", "title": "Unibet"}
OTHER = {"key": "other_book", "title": "Other"}


class TestLogger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db = logger.DB_FILE
        logger.DB_FILE = os.path.join(self.tmp.name, "test_bets.db")
        logger.init_db()

    def tearDown(self):
        logger.DB_FILE = self.original_db
        self.tmp.cleanup()

    def rows(self):
        with closing(sqlite3.connect(logger.DB_FILE)) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM bets ORDER BY id").fetchall()

    def log(self, market_key="h2h", outcome="Lag A", point="", bookmaker=UNIBET):
        return logger.log_bet(MATCH, "icehockey_sweden", market_key, outcome, point,
                              bookmaker, 2.10, 1.95, 0.0769, 3.5)

    def test_columns_and_idempotent_init(self):
        logger.init_db()
        with closing(sqlite3.connect(logger.DB_FILE)) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(bets)")]
        self.assertEqual(cols, [
            "id", "logged_date", "match_date", "commence_time", "sport_key", "match_id",
            "match_name", "market_key", "outcome_name", "point", "bookmaker",
            "offered_odds", "fair_at_flag", "edge", "units", "stake_flat", "stake_kelly",
            "result", "closing_fair", "clv", "clv_status",
        ])

    def test_credit_log_table(self):
        logger.init_db()
        with closing(sqlite3.connect(logger.DB_FILE)) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(credit_log)")]
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO credit_log (ts, script) VALUES ('x', 'annat')")
        self.assertEqual(cols, ["id", "ts", "script", "cost", "remaining"])

    def test_init_db_keeps_existing_bets(self):
        self.log()
        self.log(market_key="totals", outcome="Over", point=2.5)
        before = [tuple(r) for r in self.rows()]
        with closing(sqlite3.connect(logger.DB_FILE)) as conn, conn:
            conn.execute("DROP TABLE credit_log")         # som en databas från före credit_log
        logger.init_db()
        logger.init_db()
        self.assertEqual([tuple(r) for r in self.rows()], before)

    def test_h2h_row(self):
        self.assertTrue(self.log())
        row = self.rows()[0]
        self.assertIsNone(row["point"])
        self.assertEqual(row["match_id"], "abc123")
        self.assertEqual(row["match_name"], "Lag A vs Lag B")
        self.assertEqual(row["match_date"], "2026-10-08")
        self.assertEqual(row["commence_time"], "2026-10-08T18:00:00Z")
        self.assertEqual(row["bookmaker"], "unibet_se")
        self.assertEqual(row["stake_flat"], 20)
        self.assertEqual(row["stake_kelly"], 35.0)
        self.assertIsNone(row["result"])

    def test_totals_row(self):
        self.log(market_key="totals", outcome="Over", point=2.5)
        self.assertEqual(self.rows()[0]["point"], 2.5)

    def test_duplicate_h2h_blocked(self):
        self.assertTrue(self.log())
        self.assertFalse(self.log())
        self.assertEqual(len(self.rows()), 1)

    def test_duplicate_totals_blocked_but_other_line_allowed(self):
        self.assertTrue(self.log(market_key="totals", outcome="Over", point=2.5))
        self.assertFalse(self.log(market_key="totals", outcome="Over", point=2.5))
        self.assertTrue(self.log(market_key="totals", outcome="Over", point=3.5))
        self.assertEqual(len(self.rows()), 2)

    def test_other_bookmaker_or_outcome_allowed(self):
        self.assertTrue(self.log())
        self.assertTrue(self.log(bookmaker=OTHER))
        self.assertTrue(self.log(outcome="Lag B"))
        self.assertEqual(len(self.rows()), 3)

    def test_invalid_result_rejected(self):
        self.log()
        with closing(sqlite3.connect(logger.DB_FILE)) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE bets SET result = 'X'")


if __name__ == "__main__":
    unittest.main()
