print("Startar Oddisy...", flush=True)

import os
import sys
import traceback

import api
from api import get_sports, get_odds, is_network_error
from calculator import calculate_fair_odds, find_pinnacle
from credits import BudgetError, check_budget, log_usage
from logger import init_db, log_bet
from tracker import load_seen, save_seen, make_key, should_flag, mark_flagged
from datetime import datetime, timezone, timedelta



REGIONS = "eu"
MARKETS = "h2h"
SOCCER_ALLOWLIST = [                 # tom lista = alla soccer_-sporter
    "soccer_argentina_primera_division", "soccer_sweden_allsvenskan",
    "soccer_poland_ekstraklasa", "soccer_italy_serie_a", "soccer_germany_liga3",
    "soccer_germany_bundesliga", "soccer_france_ligue_one", "soccer_sweden_superettan",
    "soccer_spain_segunda_division", "soccer_japan_j_league", "soccer_germany_bundesliga2",
    "soccer_china_superleague", "soccer_brazil_serie_b", "soccer_belgium_first_div",
    "soccer_austria_bundesliga",
]
TENNIS_PREFIXES = ("tennis_atp_", "tennis_wta_")
MY_BOOKMAKERS = ["unibet_se"]
MAX_DAYS_AHEAD = 3
MAX_EDGE = 0.15
MAX_ODDS_AGE = timedelta(minutes=3)


def select_sports(sports):
    """Sporter som ska skannas: aktiva, inte outrights/_winner, och antingen fotboll i
    SOCCER_ALLOWLIST (alla soccer_ om listan är tom) eller tennis enligt TENNIS_PREFIXES."""
    selected = []
    for sport in sports:
        key = sport["key"]
        if not sport["active"] or sport.get("has_outrights") or key.endswith("_winner"):
            continue
        if SOCCER_ALLOWLIST:
            is_soccer = key in SOCCER_ALLOWLIST
        else:
            is_soccer = key.startswith("soccer_")
        if is_soccer or key.startswith(TENNIS_PREFIXES):
            selected.append(sport)
    return selected


def error_location(e):
    """Fil och rad där undantaget uppstod, t.ex. "main.py:42". Felets text tas aldrig med."""
    frame = traceback.extract_tb(e.__traceback__)[-1]
    return f"{os.path.basename(frame.filename)}:{frame.lineno}"


def scan_sport(sport, seen):
    """Jämför alla matcher i en sport och loggar value bets.
    Returnerar "ok", "network" (nätverksfel) eller "api" (annat API-fel)."""
    odds = get_odds(sport["key"], REGIONS, MARKETS)
    log_usage("main", *api.last_usage)
    if not isinstance(odds, list):
        # API- eller nätverksfel, t.ex. {"message": "nätverksfel (ReadTimeout)"}
        message = odds.get("message") if isinstance(odds, dict) else odds
        print(f"Varning: hoppar över {sport['key']}, {message}", flush=True)
        return "network" if is_network_error(odds) else "api"
    for match in odds:
        if not match["bookmakers"]:
            continue
        commence_time = datetime.fromisoformat(match["commence_time"].replace("Z", "+00:00"))  # gör om sträng till datumobjekt
        now = datetime.now(timezone.utc)                             # "nu" i UTC-tid
        if commence_time <= now:                                     # bara förmatch
            continue
        if commence_time > now + timedelta(days=MAX_DAYS_AHEAD):                
            continue


        
        # Steg 1 & 2: Hitta Pinnacle, hoppa över om den saknas
        pinnacle = find_pinnacle(match["bookmakers"])
        if pinnacle is None:
            continue

        # Gamla Pinnacle-odds ger felaktiga rättvisa odds
        pinnacle_updated = datetime.fromisoformat(pinnacle["last_update"].replace("Z", "+00:00"))
        if now - pinnacle_updated > MAX_ODDS_AGE:
            continue
        
        # Steg 3 & 4: Bygg dictionary per marknad
        # Nyckeln är "namn_punkt" t.ex. "Under_2.5" för att undvika falska jämförelser
        pinnacle_markets = {}
        for market in pinnacle["markets"]:
            fair_odds = calculate_fair_odds(market["outcomes"])
            pinnacle_markets[market["key"]] = {}
            for outcome, fair_odd in zip(market["outcomes"], fair_odds):
                point = outcome.get("point", "")
                key = f"{outcome['name']}_{point}" if point else outcome["name"]
                pinnacle_markets[market["key"]][key] = fair_odd

        # Steg 5: Jämför varje bookmaker mot rättvisa odds
        for bookmaker in match["bookmakers"]:
            if bookmaker["key"] == "pinnacle":
                continue

            if bookmaker["key"] not in MY_BOOKMAKERS:
                continue

            bookmaker_updated = datetime.fromisoformat(bookmaker["last_update"].replace("Z", "+00:00"))
            if now - bookmaker_updated > MAX_ODDS_AGE:
                continue

            skillnad = abs(pinnacle_updated - bookmaker_updated)
            if skillnad > timedelta(minutes=5):
                continue

            for market in bookmaker["markets"]:
                if market["key"] not in pinnacle_markets:
                    continue
                if len(market["outcomes"]) != len(pinnacle_markets[market["key"]]):
                    continue

                for outcome in market["outcomes"]:
                    point = outcome.get("point", "")
                    key = f"{outcome['name']}_{point}" if point else outcome["name"]
                    offered_odds = outcome["price"]
                    fair = pinnacle_markets[market["key"]].get(key)
                    if fair and offered_odds > fair:
                        edge = (offered_odds / fair) - 1
                        if edge < 0.025:
                            continue
                        if edge > MAX_EDGE:                          # nästan alltid datafel
                            continue

                        # Dubblettspärr: hoppa över om vi redan flaggat detta bet
                        # och edgen inte vuxit tillräckligt
                        bet_key = make_key(match, market["key"], outcome["name"], point, bookmaker["key"])
                        if not should_flag(seen, bet_key, edge):
                            continue
                        mark_flagged(seen, bet_key, edge)

                        kelly = edge / (offered_odds - 1)
                        half_kelly = kelly / 2
                        units = round(half_kelly * 100, 1)
                        point_str = f" {point}" if point else ""
                        print(f"🔥 VALUE BET!")

                        pin_sum = sum(1/v for v in pinnacle_markets[market["key"]].values())
                        unibet_sum = sum(1/o["price"] for o in market["outcomes"])
                        print(f"DIAG pinnacle_sum={pin_sum:.4f} unibet_sum={unibet_sum:.4f} utfall={len(market['outcomes'])}", flush=True)
                        
                        print(f"Match: {match['home_team']} vs {match['away_team']}")
                        print(f"Marknad: {market['key']}")
                        print(f"Bookmaker: {bookmaker['title']}")
                        print(f"Utfall: {outcome['name']}{point_str}")
                        print(f"Erbjudna odds: {offered_odds}")
                        print(f"Rättvisa odds: {round(fair, 2)}")
                        print(f"Edge: {round(edge * 100, 1)}%")
                        print(f"Half Kelly: {units}u")
                        print("---")
                        
                        log_bet(match, sport["key"], market["key"], outcome["name"], point, bookmaker, offered_odds, fair, edge, units)
    return "ok"


def run():
    """Skannar sporterna som select_sports väljer. Returnerar antal per utfall: ok, network, api, unexpected.
    Kastar BudgetError före första get_odds om kreditskyddet slår till."""
    init_db()
    seen = load_seen()
    counts = {"ok": 0, "network": 0, "api": 0, "unexpected": 0}
    try:
        sports = get_sports()
        log_usage("main", *api.last_usage)
        if not isinstance(sports, list):
            message = sports.get("message") if isinstance(sports, dict) else sports
            print(f"Varning: kunde inte hämta sporter, {message}", flush=True)
            sports = []
        selected = select_sports(sports)
        skipped = len(sports) - len(selected)
        soccer = sum(1 for s in selected if s["key"].startswith("soccer_"))
        markets = len(MARKETS.split(","))
        print(f"Valda sporter: {len(selected)} ({soccer} fotboll, {len(selected) - soccer} tennis). "
              f"Beräknad kostnad: {len(selected)} × {markets} marknad(er) × 1 region = "
              f"{len(selected) * markets} krediter.", flush=True)
        check_budget("main", len(selected) * markets)
        for sport in selected:
            try:
                counts[scan_sport(sport, seen)] += 1
            except Exception as e:
                # En trasig sport får aldrig stoppa hela körningen. Bara feltyp och plats skrivs ut.
                counts["unexpected"] += 1
                print(f"Varning: hoppar över {sport['key']}, {type(e).__name__} i {error_location(e)}",
                      flush=True)
        print(f"\nSammanfattning: {counts['ok']} sporter skannade, hoppade över "
              f"{counts['network']} pga nätverksfel, {counts['unexpected']} pga oväntade fel, "
              f"{counts['api']} pga andra API-fel. {skipped} sporter bortvalda.", flush=True)
    finally:
        save_seen(seen)
    return counts


if __name__ == "__main__":
    try:
        run()
    except BudgetError as e:
        print(f"Avbryter: {e}", file=sys.stderr)
        sys.exit(2)
