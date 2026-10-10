import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from calculator import calculate_fair_odds, find_pinnacle
from credits import BudgetError, check_budget, log_usage
from logger import init_db
from results import describe

DB_FILE = "bets.db"
WINDOW = timedelta(minutes=8)                  # bets vars match startar inom så här lång tid
STATUSES = ("ok", "line_changed", "missing")
CREDITS_PER_MARKET = 1                         # event-odds: marknader i svaret x 1 region (bookmakers=pinnacle)


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


def pinnacle_markets(data):
    """Pinnacles marknader i ett event-odds-svar som {market_key: market}, eller None vid API-fel."""
    if not isinstance(data, dict) or not isinstance(data.get("bookmakers"), list):
        return None
    pinnacle = find_pinnacle(data["bookmakers"])
    return {m["key"]: m for m in pinnacle["markets"]} if pinnacle else {}


def run(db_file=DB_FILE, fetch=None, now=None):
    """Mäter CLV för bets vars match startar inom WINDOW.
    fetch(sport_key, event_id, market_key) ska returnera (data, kostnad, kvarvarande krediter).
    Kastar BudgetError före första anropet om kreditskyddet slår till."""
    if not Path(db_file).exists():
        raise FileNotFoundError(f"Hittar inte {db_file} – har något bet loggats än?")
    init_db(db_file)                              # lägger till clv_status i äldre databaser
    now = now or datetime.now(timezone.utc)
    summary = {status: [] for status in STATUSES}
    summary["retry"] = []
    clv_values = []

    with closing(sqlite3.connect(db_file)) as conn:
        bets = select_bets(conn, now)
        by_match = {}
        for bet in bets:
            by_match.setdefault(bet["match_id"], []).append(bet)
        estimate = sum(len({b["market_key"] for b in match_bets}) * CREDITS_PER_MARKET
                       for match_bets in by_match.values())

        print(f"{len(bets)} bets startar inom {WINDOW.seconds // 60} min. Hämtar Pinnacle för "
              f"{len(by_match)} matcher, beräknad kostnad högst {estimate} krediter.")

        if by_match:
            check_budget("clv", estimate, db_file)
        if by_match and fetch is None:
            from api import get_pinnacle_odds     # importeras först här, så tester aldrig rör API:t
            fetch = get_pinnacle_odds

        for match_id, match_bets in by_match.items():
            first = match_bets[0]
            market_key = ",".join(sorted({b["market_key"] for b in match_bets}))
            data, cost, remaining = fetch(first["sport_key"], match_id, market_key)
            log_usage("clv", cost, remaining, db_file)
            print(f"{first['match_name']} ({market_key}): kostnad {cost or '?'}, kvar {remaining or '?'}")

            markets = pinnacle_markets(data)
            if markets is None:
                # API-fel: lämna bets som NULL så nästa körning försöker igen
                message = data.get("message") if isinstance(data, dict) else data
                print(f"Varning: API-fel för {first['match_name']} ({message}). "
                      f"Slut på krediter? Hoppar över, försöker igen nästa körning.")
                summary["retry"] += [b["id"] for b in match_bets]
                continue

            for bet in match_bets:
                closing_fair, value, status = compute_clv(bet, markets.get(bet["market_key"]))
                if not save_clv(conn, bet["id"], closing_fair, value, status):
                    continue                      # hann få status av en annan körning
                summary[status].append(bet["id"])
                if status == "ok":
                    clv_values.append(value)
                    print(f"  ok  CLV {value * 100:+.1f}%  {describe(bet)}")
                else:
                    print(f"  {status}  {describe(bet)}")

    average = sum(clv_values) / len(clv_values) if clv_values else None
    summary["average_clv"] = average
    print(f"\nSammanfattning: {len(summary['ok'])} ok, {len(summary['line_changed'])} line_changed, "
          f"{len(summary['missing'])} missing. Snitt-CLV (ok): "
          + (f"{average * 100:+.2f}%" if average is not None else "–"))
    if summary["retry"]:
        print(f"API-fel: {len(summary['retry'])} bets hoppade över, försöker igen nästa körning.")
    return summary


if __name__ == "__main__":
    try:
        run()
    except FileNotFoundError as e:
        print(f"Fel: {e}", file=sys.stderr)
        sys.exit(1)
    except BudgetError as e:
        print(f"Avbryter: {e}", file=sys.stderr)
        sys.exit(2)
