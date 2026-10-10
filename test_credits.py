import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

import credits
import logger
from test_api import KEY

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class CreditsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "test_bets.db")
        patcher = mock.patch.object(logger, "DB_FILE", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        logger.init_db()

    def rows(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT ts, script, cost, remaining FROM credit_log ORDER BY id").fetchall()

    def log(self, script, cost, remaining, ago=timedelta(0)):
        return credits.log_usage(script, cost, remaining, now=NOW - ago)


class TestLogUsage(CreditsTestCase):
    def test_strings_converted_to_int(self):
        self.assertTrue(self.log("clv", "2", "498"))
        self.assertEqual(self.rows(), [("2026-10-10T12:00:00+00:00", "clv", 2, 498)])

    def test_nothing_saved_when_both_missing(self):
        self.assertFalse(self.log("main", None, None))
        self.assertFalse(self.log("main", "", "trasig"))
        self.assertEqual(self.rows(), [])

    def test_one_value_missing_saved_as_null(self):
        self.log("main", None, "400")
        self.log("main", "3", None)
        self.assertEqual([r[2:] for r in self.rows()], [(None, 400), (3, None)])

    def test_default_ts_is_utc(self):
        credits.log_usage("main", 1, 2)
        ts = datetime.fromisoformat(self.rows()[0][0])
        self.assertEqual(ts.utcoffset(), timedelta(0))
        self.assertLess(abs(datetime.now(timezone.utc) - ts), timedelta(minutes=1))

    def test_explicit_db_file(self):
        other = os.path.join(self.tmp.name, "annan.db")
        logger.init_db(other)
        credits.log_usage("results", 2, 10, db_file=other, now=NOW)
        self.assertEqual(self.rows(), [])
        self.assertEqual(credits.last_remaining(other), 10)


class TestUsedLastDays(CreditsTestCase):
    def test_rolling_window(self):
        self.log("main", 10, 400, ago=timedelta(days=7))                  # precis på gränsen, räknas
        self.log("main", 20, 380, ago=timedelta(days=7, seconds=1))       # för gammal
        self.log("main", 5, 375, ago=timedelta(hours=1))
        self.assertEqual(credits.used_last_days(7, now=NOW), 15)

    def test_filter_by_script(self):
        self.log("main", 10, 400)
        self.log("clv", 3, 397)
        self.log("results", 2, 395)
        self.assertEqual(credits.used_last_days(7, now=NOW), 15)
        self.assertEqual(credits.used_last_days(7, "main", now=NOW), 10)
        self.assertEqual(credits.used_last_days(7, "clv", now=NOW), 3)

    def test_empty(self):
        self.assertEqual(credits.used_last_days(7, now=NOW), 0)

    def test_reset_does_not_clear_week(self):
        self.log("main", 100, 400)
        credits.reset(now=NOW)
        self.log("main", 50, 450)                                         # ny nyckel
        self.assertEqual(credits.used_last_days(7, "main", now=NOW), 150)


class TestLastRemaining(CreditsTestCase):
    def test_empty_is_unknown(self):
        self.assertIsNone(credits.last_remaining())

    def test_latest_known(self):
        self.log("main", 1, 400)
        self.log("clv", 1, 399)
        self.log("main", 3, None)                                         # utan remaining, hoppas över
        self.assertEqual(credits.last_remaining(), 399)

    def test_reset_makes_unknown_then_new_value(self):
        self.log("main", 1, 5)
        credits.reset(now=NOW)
        self.assertIsNone(credits.last_remaining())
        self.log("main", 3, None)
        self.assertIsNone(credits.last_remaining())
        self.log("clv", 1, 499)
        self.assertEqual(credits.last_remaining(), 499)


class TestCheckBudget(CreditsTestCase):
    def check(self, script, estimate):
        return credits.check_budget(script, estimate, now=NOW)

    def blocked(self, script, estimate):
        with self.assertRaises(credits.BudgetError) as ctx:
            self.check(script, estimate)
        message = str(ctx.exception)
        self.assertNotIn(KEY, message)
        self.assertNotIn("apiKey", message)
        return message

    def test_unknown_remaining_allowed(self):
        for script in ("main", "clv", "results"):
            self.assertIsNone(self.check(script, 20))

    def test_main_weekly_limit_boundary(self):
        self.log("main", 480, None)
        self.assertIsNone(self.check("main", 20))                         # 480 + 20 = 500, ok
        message = self.blocked("main", 21)
        self.assertIn("Kreditskydd (veckobudget)", message)
        self.assertIn("480", message)
        self.assertIn("Att byta nyckel i .env hjälper inte", message)

    def test_main_weekly_only_counts_main_and_last_7_days(self):
        self.log("clv", 400, None)
        self.log("results", 400, None)
        self.log("main", 400, None, ago=timedelta(days=8))
        self.assertIsNone(self.check("main", 100))

    def test_main_weekly_spans_keys(self):
        self.log("main", 300, 0)
        credits.reset(now=NOW)
        self.log("main", 190, 310)
        self.assertIn("veckobudget", self.blocked("main", 20))

    def test_main_reserve_boundary(self):
        self.log("clv", 1, 50)
        self.assertIsNone(self.check("main", 20))                         # 50 - 20 = 30, ok
        message = self.blocked("main", 21)
        self.assertIn("Kreditskydd (reserv)", message)
        self.assertIn("Byt nyckel i .env", message)
        self.assertIn("credits.py reset", message)

    def test_clv_and_results_only_reserve(self):
        self.log("main", 600, 30)
        for script in ("clv", "results"):
            self.assertIsNone(self.check(script, 1000))                   # beräknad kostnad ignoreras
        self.log("main", 1, 29)
        for script in ("clv", "results"):
            message = self.blocked(script, 0)
            self.assertIn("Kreditskydd (reserv)", message)
            self.assertIn("29 < 30", message)
            self.assertIn("Byt nyckel i .env", message)

    def test_reset_lets_runs_through(self):
        self.log("main", 1, 0)
        credits.reset(now=NOW)
        for script in ("main", "clv", "results"):
            self.assertIsNone(self.check(script, 20))


class TestCli(CreditsTestCase):
    def test_reset(self):
        self.log("main", 7, 10)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(credits.main(["reset"]), 0)
        self.assertIn("okänt", out.getvalue())
        self.assertEqual([r[1:] for r in self.rows()], [("main", 7, 10), ("reset", 0, None)])
        self.assertEqual(credits.used_last_days(7), 7)

    def test_reset_creates_table_in_old_db(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("DROP TABLE credit_log")
        with redirect_stdout(io.StringIO()):
            credits.main(["reset"])
        self.assertEqual(len(self.rows()), 1)

    def test_bad_arguments(self):
        for argv in ([], ["nollställ"], ["reset", "extra"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()) as err:
                self.assertEqual(credits.main(argv), 1)
                self.assertIn("Användning", err.getvalue())
        self.assertEqual(self.rows(), [])


if __name__ == "__main__":
    unittest.main()
