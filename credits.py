import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone

import logger

WEEKLY_BUDGET = 500          # krediter per rullande 7 dagar, gäller bara main.py, alla nycklar totalt
RESERVE = 30                 # lämna alltid minst så här många krediter på nyckeln
USAGE = "Användning: python3 credits.py reset"


class BudgetError(Exception):
    pass


def _db(db_file):
    # logger.DB_FILE läses vid anropet, så tester som pekar om den aldrig rör riktiga bets.db
    return db_file or logger.DB_FILE


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def log_usage(script, cost, remaining, db_file=None, now=None):
    """Sparar ett API-anrops kostnad och kvarvarande krediter (int eller sträng från headrarna).
    Sparar inget om båda saknas. Returnerar True om en rad skrevs."""
    cost, remaining = _to_int(cost), _to_int(remaining)
    if cost is None and remaining is None:
        return False
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with closing(sqlite3.connect(_db(db_file))) as conn, conn:
        conn.execute("INSERT INTO credit_log (ts, script, cost, remaining) VALUES (?, ?, ?, ?)",
                     (ts, script, cost, remaining))
    return True


def used_last_days(days, script=None, db_file=None, now=None):
    """Summa cost de senaste days dygnen (rullande), över alla nycklar. Valfritt bara ett skript."""
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat(timespec="seconds")
    query = "SELECT COALESCE(SUM(cost), 0) FROM credit_log WHERE ts >= ?"
    params = [since]
    if script:
        query += " AND script = ?"
        params.append(script)
    with closing(sqlite3.connect(_db(db_file))) as conn:
        return conn.execute(query, params).fetchone()[0]


def last_remaining(db_file=None):
    """Senast kända kvarvarande krediter, eller None om okänt (ingen logg, eller reset efter
    senaste kända värdet)."""
    with closing(sqlite3.connect(_db(db_file))) as conn:
        row = conn.execute("""
            SELECT remaining FROM credit_log
            WHERE remaining IS NOT NULL OR script = 'reset'
            ORDER BY id DESC LIMIT 1
        """).fetchone()
    return row[0] if row else None


def check_budget(script, estimated_cost, db_file=None, now=None):
    """Kastar BudgetError om körningen ska avbrytas.
    main: veckobudget (mains förbrukning senaste 7 dagarna) och reserv efter beräknad kostnad.
    clv och results: bara om senast kända remaining redan är under reserven.
    Okänt remaining släpper alltid igenom reservskyddet."""
    remaining = last_remaining(db_file)
    if script == "main":
        used = used_last_days(7, "main", db_file, now)
        if used + estimated_cost > WEEKLY_BUDGET:
            raise BudgetError(
                f"Kreditskydd (veckobudget): main har förbrukat {used} krediter senaste 7 dagarna "
                f"+ beräknat {estimated_cost} > {WEEKLY_BUDGET}. Att byta nyckel i .env hjälper inte, "
                f"budgeten gäller alla nycklar. Vänta eller höj WEEKLY_BUDGET.")
        if remaining is not None and remaining - estimated_cost < RESERVE:
            raise BudgetError(
                f"Kreditskydd (reserv): senast kända remaining {remaining} − beräknat "
                f"{estimated_cost} < {RESERVE}. Byt nyckel i .env och kör python3 credits.py reset.")
    elif remaining is not None and remaining < RESERVE:
        raise BudgetError(
            f"Kreditskydd (reserv): senast kända remaining {remaining} < {RESERVE}. "
            f"Byt nyckel i .env och kör python3 credits.py reset.")


def reset(db_file=None, now=None):
    """Markerar att senast kända remaining är okänt (efter nyckelbyte). Veckoräkningen påverkas inte."""
    logger.init_db(_db(db_file))
    ts = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with closing(sqlite3.connect(_db(db_file))) as conn, conn:
        conn.execute("INSERT INTO credit_log (ts, script, cost, remaining) VALUES (?, 'reset', 0, NULL)",
                     (ts,))


def main(argv):
    if argv != ["reset"]:
        print(USAGE, file=sys.stderr)
        return 1
    reset()
    print("Senast kända remaining är nu okänt. Veckoräkningen påverkas inte.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
