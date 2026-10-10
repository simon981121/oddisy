import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import clv
import credits
import logger
import status
from test_api import KEY

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
UNIBET = {"key": "unibet_se", "title": "Unibet"}


class TestStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "test_bets.db")
        patcher = mock.patch.object(logger, "DB_FILE", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        logger.init_db()

    def add(self, match_id, commence_time, result=None, clv_status=None):
        logger.log_bet({"id": match_id, "commence_time": commence_time,
                        "home_team": "Hemma", "away_team": "Borta"},
                       "soccer_sweden_allsvenskan", "h2h", "Hemma", "", UNIBET, 2.1, 1.95, 0.08, 3.5)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            if result:
                conn.execute("UPDATE bets SET result = ? WHERE match_id = ?", (result, match_id))
        if clv_status:
            with closing(sqlite3.connect(self.db)) as conn:
                bet_id = conn.execute("SELECT id FROM bets WHERE match_id = ?", (match_id,)).fetchone()[0]
                clv.save_clv(conn, bet_id, None, None, clv_status)

    def run_status(self):
        with redirect_stdout(io.StringIO()) as out:
            result = status.run(self.db, NOW)
        return result, out.getvalue()

    def test_counts(self):
        credits.log_usage("main", 40, 460, now=NOW - timedelta(days=1))
        credits.log_usage("main", 99, 999, now=NOW - timedelta(days=8))     # utanför 7 dagar
        credits.log_usage("clv", 3, 457, now=NOW - timedelta(hours=2))
        credits.log_usage("results", 2, 455, now=NOW - timedelta(hours=1))
        self.add("past_open", "2026-10-10T10:00:00Z")                       # öppen, missad CLV
        self.add("past_done", "2026-10-09T10:00:00Z", result="W", clv_status="ok")
        self.add("past_settled_nullclv", "2026-10-09T11:00:00Z", result="L")  # missad CLV
        self.add("future", "2026-10-11T10:00:00Z")                          # öppen, inte missad
        result, out = self.run_status()
        self.assertEqual(result["used"], {"main": 40, "clv": 3, "results": 2})
        self.assertEqual(result["remaining"], 455)
        self.assertEqual(result["open_bets"], 2)
        self.assertEqual(result["missed_clv"], 2)
        self.assertIn("totalt      45", out)
        self.assertIn("Veckobudget main: 40 av 500 (460 kvar)", out)
        self.assertIn("Senast kända remaining: 455", out)
        self.assertIn("Öppna bets utan resultat: 2", out)
        self.assertIn("Missade CLV-fönster (startade, clv_status saknas): 2", out)
        self.assertNotIn(KEY, out)

    def test_reset_shows_unknown_but_keeps_week(self):
        credits.log_usage("main", 40, 10, now=NOW)
        credits.reset(now=NOW)
        result, out = self.run_status()
        self.assertIsNone(result["remaining"])
        self.assertIn("Senast kända remaining: okänt", out)
        self.assertEqual(result["used"]["main"], 40)

    def test_old_db_without_credit_log_not_modified(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("DROP TABLE credit_log")
        with open(self.db, "rb") as f:
            before = f.read()
        result, out = self.run_status()
        self.assertEqual(result["used"], {"main": 0, "clv": 0, "results": 0})
        self.assertIn("okänt", out)
        with open(self.db, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_missing_db(self):
        missing = os.path.join(self.tmp.name, "finns_inte.db")
        with self.assertRaises(status.StatusError):
            status.run(missing, NOW)
        self.assertFalse(os.path.exists(missing))


if __name__ == "__main__":
    unittest.main()
