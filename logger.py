import sqlite3
from contextlib import closing
from datetime import datetime, timezone

DB_FILE = "bets.db"


def init_db():
    """Skapar tabellen bets och dubblettindexet om de saknas."""
    with closing(sqlite3.connect(DB_FILE)) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_date   TEXT NOT NULL,
                match_date    TEXT,
                commence_time TEXT,
                sport_key     TEXT,
                match_id      TEXT NOT NULL,
                match_name    TEXT,
                market_key    TEXT NOT NULL,
                outcome_name  TEXT NOT NULL,
                point         REAL,
                bookmaker     TEXT NOT NULL,
                offered_odds  REAL,
                fair_at_flag  REAL,
                edge          REAL,
                units         REAL,
                stake_flat    REAL DEFAULT 20,
                stake_kelly   REAL,
                result        TEXT CHECK (result IN ('W', 'L', 'P') OR result IS NULL),
                closing_fair  REAL,
                clv           REAL
            )
        """)
        # NULL räknas som olika i UNIQUE, därför COALESCE så h2h (point NULL) också spärras
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_bets_unique
            ON bets (match_id, market_key, outcome_name, COALESCE(point, ''), bookmaker)
        """)


def log_bet(match, sport_key, market_key, outcome_name, point, bookmaker, offered_odds, fair, edge, units):
    """Loggar ett bet. Returnerar True om en ny rad skapades, False om den redan fanns."""
    point_value = None if point in ("", None) else float(point)
    logged_date = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with closing(sqlite3.connect(DB_FILE)) as conn, conn:
        cursor = conn.execute("""
            INSERT OR IGNORE INTO bets (
                logged_date, match_date, commence_time, sport_key, match_id, match_name,
                market_key, outcome_name, point, bookmaker,
                offered_odds, fair_at_flag, edge, units, stake_flat, stake_kelly
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            logged_date,
            match["commence_time"][:10],
            match["commence_time"],
            sport_key,
            match["id"],
            f"{match['home_team']} vs {match['away_team']}",
            market_key,
            outcome_name,
            point_value,
            bookmaker["key"],
            offered_odds,
            fair,
            edge,
            units,
            20,
            round(units * 10, 2),
        ))
        return cursor.rowcount == 1
