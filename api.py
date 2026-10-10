import requests
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL = "https://api.the-odds-api.com/v4"
REQUEST_TIMEOUT = (5, 30)        # sekunder: (anslutning, max väntan mellan datapaket)
NETWORK_ERROR = "nätverksfel"
last_usage = (None, None)        # (kostnad, kvarvarande krediter) från senaste anropets headrar


def is_network_error(data):
    """True om data är felvärdet från _get för timeout eller anslutningsfel."""
    return isinstance(data, dict) and str(data.get("message", "")).startswith(NETWORK_ERROR)


def _header_int(headers, name):
    try:
        return int(float(headers.get(name)))
    except (TypeError, ValueError):
        return None


def usage(response):
    """(kostnad, kvarvarande krediter) som int från x-requests-last och x-requests-remaining.
    En header som saknas eller är ogiltig blir None."""
    if response is None:
        return None, None
    headers = response.headers or {}
    return _header_int(headers, "x-requests-last"), _header_int(headers, "x-requests-remaining")


def _get(url, params):
    """requests.get med timeout. Returnerar (data, response).
    Vid nätverksfel eller ogiltigt svar är data {"message": ...}, samma form som API:ts egna fel,
    och response är None om inget svar kom. Sätter last_usage från svarets headrar."""
    global last_usage
    last_usage = (None, None)    # aldrig ett gammalt värde efter ett nätverksfel
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        # Felmeddelandet kan innehålla URL:en med API-nyckeln, skriv bara ut feltypen
        return {"message": f"{NETWORK_ERROR} ({type(e).__name__})"}, None
    except requests.exceptions.RequestException as e:
        return {"message": f"anropsfel ({type(e).__name__})"}, None
    last_usage = usage(response)
    try:
        return response.json(), response
    except ValueError:
        return {"message": f"ogiltigt svar (HTTP {response.status_code})"}, response


def get_odds(sport, regions, markets):
    url = f"{BASE_URL}/sports/{sport}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": regions,
        "markets": markets,
    }
    data, _ = _get(url, params)
    return data


def get_scores(sport, days_from=3, event_ids=None):
    url = f"{BASE_URL}/sports/{sport}/scores"
    params = {
        "apiKey": API_KEY,
        "daysFrom": days_from,
    }
    if event_ids:
        params["eventIds"] = ",".join(event_ids)
    data, response = _get(url, params)
    if response is not None:
        print(f"Scores {sport}: kostnad {response.headers.get('x-requests-last')}, "
              f"kvar {response.headers.get('x-requests-remaining')}")
    return data


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
    data, response = _get(url, params)
    if response is None:
        return data, None, None
    return data, response.headers.get("x-requests-last"), response.headers.get("x-requests-remaining")


def get_sports():
    url = f"{BASE_URL}/sports"
    params = {
        "apiKey": API_KEY
    }
    data, _ = _get(url, params)
    return data
