import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from credits import RESERVE, WEEKLY_BUDGET

DB_FILE = "bets.db"
SCRIPTS = ("main", "clv", "results")


class StatusError(Exception):
    pass


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def collect(db_file=DB_FILE, now=None):
    """Läser bets.db skrivskyddat. Returnerar en dict med förbrukning och räknare."""
    if not Path(db_file).exists():
        raise StatusError(f"Hittar inte {db_file} – har något bet loggats än?")
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=7)).isoformat(timespec="seconds")
    uri = Path(db_file).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        used = dict.fromkeys(SCRIPTS, 0)
        remaining = None
        if "credit_log" in tables:
            for script, cost in conn.execute("""
                SELECT script, COALESCE(SUM(cost), 0) FROM credit_log
                WHERE ts >= ? AND script != 'reset' GROUP BY script
            """, (since,)):
                used[script] = cost
            row = conn.execute("""
                SELECT remaining FROM credit_log WHERE remaining IS NOT NULL OR script = 'reset'
                ORDER BY id DESC LIMIT 1
            """).fetchone()
            remaining = row[0] if row else None
        open_bets = missed_clv = 0
        if "bets" in tables:
            open_bets = conn.execute("SELECT COUNT(*) FROM bets WHERE result IS NULL").fetchone()[0]
            starts = conn.execute("""
                SELECT commence_time FROM bets
                WHERE clv_status IS NULL AND commence_time IS NOT NULL
            """).fetchall()
            missed_clv = sum(1 for (t,) in starts if parse_time(t) < now)
    return {"used": used, "remaining": remaining, "open_bets": open_bets, "missed_clv": missed_clv}


def run(db_file=DB_FILE, now=None):
    status = collect(db_file, now)
    used = status["used"]
    print("Förbrukat senaste 7 dagarna:")
    for script in SCRIPTS:
        print(f"  {script:<8}{used[script]:>6}")
    print(f"  {'totalt':<8}{sum(used.values()):>6}")
    print(f"Veckobudget main: {used['main']} av {WEEKLY_BUDGET} ({WEEKLY_BUDGET - used['main']} kvar)")
    remaining = status["remaining"]
    print(f"Senast kända remaining: {remaining if remaining is not None else 'okänt'} (reserv {RESERVE})")
    print(f"Öppna bets utan resultat: {status['open_bets']}")
    print(f"Missade CLV-fönster (startade, clv_status saknas): {status['missed_clv']}")
    return status


if __name__ == "__main__":
    try:
        run()
    except StatusError as e:
        print(f"Fel: {e}", file=sys.stderr)
        sys.exit(1)
