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


def get_sports(): 
    url = f"{BASE_URL}/sports"
    params = {
        "apiKey": API_KEY
    }
    response = requests.get(url, params=params)
    return response.json()