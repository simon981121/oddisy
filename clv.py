import sqlite3
from datetime import datetime, timedelta

from calculator import calculate_fair_odds

DB_FILE = "bets.db"
WINDOW = timedelta(minutes=15)                 # bets vars match startar inom så här lång tid
STATUSES = ("ok", "line_changed", "missing")


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compute_clv(bet, pinnacle_market):
    """Jämför bettets odds mot Pinnacles vig-borttagna stängningsodds.
    Returnerar (closing_fair, clv, status); closing_fair och clv är None om status inte är 'ok'."""
    if not pinnacle_market or pinnacle_market.get("key") != bet["market_key"]:
        return None, None, "missing"
    outcomes = pinnacle_market.get("outcomes") or []

    # Bara utfallen på bettets linje: h2h saknar point, totals har ett Over/Under-par per linje.
    # Vig-borttagningen görs på just det paret, aldrig över flera linjer.
    same_line = [o for o in outcomes if o.get("point") == bet["point"]]
    if not same_line:
        if bet["point"] is not None and outcomes:
            return None, None, "line_changed"
        return None, None, "missing"

    # Ett ensamt utfall eller trasiga priser ger inga meningsfulla fair odds
    if len(same_line) < 2 or not all(isinstance(o.get("price"), (int, float)) and o["price"] > 1
                                     for o in same_line):
        return None, None, "missing"

    fair_odds = calculate_fair_odds(same_line)
    for outcome, fair in zip(same_line, fair_odds):
        if outcome["name"] == bet["outcome_name"]:
            return fair, bet["offered_odds"] / fair - 1, "ok"
    return None, None, "missing"


def select_bets(conn, now):
    """Bets utan clv_status vars match startar mellan now och now + WINDOW (gränserna inräknade)."""
    cursor = conn.cursor()
    cursor.row_factory = sqlite3.Row
    rows = cursor.execute("""
        SELECT * FROM bets
        WHERE clv_status IS NULL AND commence_time IS NOT NULL
        ORDER BY id
    """).fetchall()
    return [b for b in rows if now <= parse_time(b["commence_time"]) <= now + WINDOW]


def save_clv(conn, bet_id, closing_fair, clv, status):
    """Sparar CLV för ett bet. Ett bet som redan har clv_status skrivs aldrig över.
    Returnerar True om raden uppdaterades."""
    if status not in STATUSES:
        raise ValueError(f"Okänd clv_status: {status!r}")
    with conn:
        cursor = conn.execute("""
            UPDATE bets SET closing_fair = ?, clv = ?, clv_status = ?
            WHERE id = ? AND clv_status IS NULL
        """, (closing_fair, clv, status, bet_id))
    return cursor.rowcount == 1
