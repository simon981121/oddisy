import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from credits import BudgetError, check_budget, log_usage
from export_excel import outcome_label
from logger import init_db

DB_FILE = "bets.db"
MIN_AGE = timedelta(hours=3)          # matchen ska ha startat minst så här länge sedan
UNCLEAR_AFTER = timedelta(hours=48)   # fortfarande utan resultat efter detta = "oklart"
DAYS_FROM = 3                         # max som scores-endpointen tillåter
CREDITS_PER_SPORT = 2                 # kostnad per scores-anrop med daysFrom (enligt dokumentationen)


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_manual(bet):
    """Ishockey: scores kan inkludera förlängning, Unibets h2h gäller ordinarie tid.
    Totals avgörs inte heller förrän vi verifierat vad endpointen levererar."""
    return bet["sport_key"].startswith("icehockey")


def team_scores(game):
    """Returnerar (hemma, borta) för en avslutad match, annars None."""
    if not game or not game.get("completed") or not game.get("scores"):
        return None
    by_name = {s["name"]: s["score"] for s in game["scores"]}
    try:
        return float(by_name[game["home_team"]]), float(by_name[game["away_team"]])
    except (KeyError, TypeError, ValueError):
        return None


def settle(bet, game):
    """Avgör ett bet mot en match. Returnerar "W", "L", "P" eller None om det inte går."""
    if is_manual(bet):
        return None
    scores = team_scores(game)
    if scores is None:
        return None
    home, away = scores

    if bet["market_key"] == "h2h":
        if home > away:
            winner = game["home_team"]
        elif away > home:
            winner = game["away_team"]
        else:
            winner = "Draw"
        if bet["outcome_name"] not in (game["home_team"], game["away_team"], "Draw"):
            return None                   # namnet matchar inte matchen, låt en människa titta
        return "W" if bet["outcome_name"] == winner else "L"

    if bet["market_key"] == "totals" and bet["point"] is not None:
        total = home + away
        if total == bet["point"]:
            return "P"
        if bet["outcome_name"] == "Over":
            return "W" if total > bet["point"] else "L"
        if bet["outcome_name"] == "Under":
            return "W" if total < bet["point"] else "L"

    return None


def is_credit_error(data):
    """True om API:t svarat att krediterna är slut, t.ex.
    {"message": "Usage quota has been reached", "error_code": "OUT_OF_USAGE_CREDITS"}."""
    if not isinstance(data, dict):
        return False
    return (data.get("error_code") == "OUT_OF_USAGE_CREDITS"
            or "quota" in str(data.get("message", "")).lower())


def describe(bet):
    label = outcome_label(bet["outcome_name"], bet["point"])
    return f"id {bet['id']}: {bet['match_name']} ({bet['sport_key']}, {bet['market_key']} {label})"


def open_db(db_file):
    if not Path(db_file).exists():
        raise FileNotFoundError(f"Hittar inte {db_file} – har något bet loggats än?")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


def load_open_bets(conn, now):
    rows = conn.execute("SELECT * FROM bets WHERE result IS NULL ORDER BY id").fetchall()
    return [b for b in rows if now - parse_time(b["commence_time"]) >= MIN_AGE]


def run(db_file=DB_FILE, fetch=None, now=None, usage=None):
    """Avgör öppna bets. fetch(sport_key, days_from, event_ids) ska returnera scores-listan.
    usage() ska returnera (kostnad, kvarvarande krediter) för senaste fetch, eller saknas.
    Kastar BudgetError före första anropet om kreditskyddet slår till."""
    now = now or datetime.now(timezone.utc)
    summary = {"settled": [], "manual": [], "unclear": [], "fetched": []}

    with closing(open_db(db_file)) as conn:
        init_db(db_file)                  # skapar credit_log i äldre databaser
        bets = load_open_bets(conn, now)
        manual = [b for b in bets if is_manual(b)]
        by_sport = {}
        for bet in bets:
            if not is_manual(bet):
                by_sport.setdefault(bet["sport_key"], []).append(bet)

        print(f"{len(bets)} öppna bets som startade för minst 3 h sedan. "
              f"Hämtar scores för {len(by_sport)} sporter, beräknad kostnad "
              f"{len(by_sport) * CREDITS_PER_SPORT} krediter.")

        if by_sport:
            check_budget("results", len(by_sport) * CREDITS_PER_SPORT, db_file)
        if by_sport and fetch is None:
            import api                    # importeras först här, så tester aldrig rör API:t
            fetch = api.get_scores
            usage = lambda: api.last_usage

        for sport_key, sport_bets in by_sport.items():
            event_ids = sorted({b["match_id"] for b in sport_bets})
            games = fetch(sport_key, DAYS_FROM, event_ids)
            if usage:
                log_usage("results", *usage(), db_file)
            summary["fetched"].append(sport_key)
            if not isinstance(games, list):
                message = games.get("message") if isinstance(games, dict) else games
                hint = " Slut på krediter?" if is_credit_error(games) else ""
                print(f"Varning: inga scores för {sport_key} ({message}).{hint}")
                games = []
            by_id = {g["id"]: g for g in games}

            for bet in sport_bets:
                result = settle(bet, by_id.get(bet["match_id"]))
                if result:
                    with conn:
                        conn.execute("UPDATE bets SET result = ? WHERE id = ? AND result IS NULL",
                                     (result, bet["id"]))
                    summary["settled"].append((bet["id"], result))
                    print(f"✅ {result}  {describe(bet)}")
                elif now - parse_time(bet["commence_time"]) >= UNCLEAR_AFTER:
                    summary["unclear"].append(bet["id"])

        for bet in manual:
            summary["manual"].append(bet["id"])
            print(f"⚠️  Ishockey, avgör manuellt: {describe(bet)}")

        if summary["unclear"]:
            print("\nOklart (inget resultat efter 48 h):")
            for bet in bets:
                if bet["id"] in summary["unclear"]:
                    print(f"  {describe(bet)}")

        if manual or summary["unclear"]:
            print("\nSätt resultat med: python set_result.py <id> <W|L|P>")

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
