import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from export_excel import outcome_label

DB_FILE = "bets.db"
VALID_RESULTS = ("W", "L", "P")
USAGE = "Användning: python set_result.py <id> <W|L|P> [--force]"


class SetResultError(Exception):
    pass


def set_result(bet_id, result, db_file=DB_FILE, force=False):
    """Sätter resultat på ett bet. Returnerar raden som den såg ut före ändringen."""
    result = result.upper()
    if result not in VALID_RESULTS:
        raise SetResultError(f"Resultat måste vara W, L eller P, inte {result!r}.")
    if not Path(db_file).exists():
        raise SetResultError(f"Hittar inte {db_file}.")

    with closing(sqlite3.connect(db_file)) as conn, conn:
        conn.row_factory = sqlite3.Row
        bet = conn.execute("SELECT * FROM bets WHERE id = ?", (bet_id,)).fetchone()
        if bet is None:
            raise SetResultError(f"Inget bet med id {bet_id}.")
        if bet["result"] is not None and not force:
            raise SetResultError(f"Bet {bet_id} har redan resultat {bet['result']}. "
                                 f"Använd --force för att ändra.")
        conn.execute("UPDATE bets SET result = ? WHERE id = ?", (result, bet_id))
    return bet


def main(argv):
    force = "--force" in argv
    args = [a for a in argv if a != "--force"]
    if len(args) != 2 or not args[0].isdigit():
        raise SetResultError(USAGE)
    bet_id, result = int(args[0]), args[1].upper()
    bet = set_result(bet_id, result, force=force)
    label = outcome_label(bet["outcome_name"], bet["point"])
    print(f"Bet {bet_id}: {bet['match_name']} – {bet['market_key']} {label} @ {bet['offered_odds']}"
          f" → {result} (tidigare: {bet['result'] or 'tomt'})")


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except SetResultError as e:
        print(f"Fel: {e}", file=sys.stderr)
        sys.exit(1)
