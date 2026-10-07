import requests
import os 
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"

def get_odds(sport, regions, markets): 
    url = f"{BASE_URL}/sports/{sport}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": regions, 
        "markets": markets,
    }
    response = requests.get(url, params=params)
    return response.json()


def get_scores(sport, days_from=3, event_ids=None):
    url = f"{BASE_URL}/sports/{sport}/scores"
    params = {
        "apiKey": API_KEY,
        "daysFrom": days_from,
    }
    if event_ids:
        params["eventIds"] = ",".join(event_ids)
    response = requests.get(url, params=params)
    print(f"Scores {sport}: kostnad {response.headers.get('x-requests-last')}, "
          f"kvar {response.headers.get('x-requests-remaining')}")
    return response.json()


def get_pinnacle_odds(sport_key, event_id, market_key):
    """Pinnacles odds för EN match. market_key kan vara kommaseparerad, t.ex. "h2h,totals".
    Kostnad enligt dokumentationen: antal marknader i svaret x 1 region (bookmakers=pinnacle),
    tomt svar kostar 0. Returnerar (data, kostnad, kvarvarande krediter)."""
    url = f"{BASE_URL}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": API_KEY,
        "bookmakers": "pinnacle",
        "markets": market_key,
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
    except requests.RequestException as e:
        # Felmeddelandet kan innehålla URL:en med API-nyckeln, skriv bara ut feltypen
        return {"message": f"nätverksfel ({type(e).__name__})"}, None, None
    except ValueError:
        return {"message": f"ogiltigt svar (HTTP {response.status_code})"}, None, None
    return data, response.headers.get("x-requests-last"), response.headers.get("x-requests-remaining")


def get_sports():
    url = f"{BASE_URL}/sports"
    params = {
        "apiKey": API_KEY
    }
    response = requests.get(url, params=params)
    return response.json()