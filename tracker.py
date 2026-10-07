import json
import os

TRACKER_FILE = "seen_bets.json"


def load_seen():
    """Läser in tidigare flaggade bets. Returnerar tom dict om filen saknas."""
    if not os.path.exists(TRACKER_FILE):
        return {}
    try:
        with open(TRACKER_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # Trasig fil (t.ex. avbruten skrivning) - börja om
        return {}


def save_seen(seen):
    """Sparar dictionaryn till disk."""
    with open(TRACKER_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def make_key(match, market_key, outcome_name, point, bookmaker_key):
    """Bygger en unik nyckel som INTE innehåller odds."""
    point_part = f"_{point}" if point != "" and point is not None else ""
    return f"{match['id']}_{market_key}_{outcome_name}{point_part}_{bookmaker_key}"


def should_flag(seen, key, edge, threshold=0.02):
    """
    Avgör om detta bet ska flaggas.
    Ny bet -> alltid True.
    Sedd bet -> bara om edgen vuxit med minst 'threshold' (2 procentenheter).
    """
    previous_edge = seen.get(key)
    if previous_edge is None:
        return True
    return edge >= previous_edge + threshold


def mark_flagged(seen, key, edge):
    """Sparar edgen för detta bet."""
    seen[key] = edge