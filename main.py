print("Startar Oddisy...", flush=True)

from api import get_sports, get_odds
from calculator import calculate_fair_odds, find_pinnacle
from logger import init_db, log_bet
from tracker import load_seen, save_seen, make_key, should_flag, mark_flagged
from datetime import datetime, timezone, timedelta



REGIONS = "eu"
MARKETS = "h2h,totals"
MY_BOOKMAKERS = ["unibet_se"]
MAX_DAYS_AHEAD = 3
MAX_EDGE = 0.15
MAX_ODDS_AGE = timedelta(minutes=3)

init_db()
seen = load_seen()

sports = get_sports()

for sport in sports:
    if sport["active"]:
        odds = get_odds(sport["key"], REGIONS, MARKETS)  
        if not isinstance(odds, list):
            continue
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

save_seen(seen)