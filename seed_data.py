"""
Strategy Factory Bot Manager — Database Seeder
Initializes SQLite database and populates with ~200 bots and strategies
with 30 days of realistic simulated performance history.

Usage: python seed_data.py
"""

import os
import sys
import sqlite3
import random
import math
import datetime
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# ── Color helpers ────────────────────────────────────────────────────
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; X = "\033[0m"; D = "\033[90m"


# ── All tradeable crypto pairs (Alpaca-supported + a few extra) ──────
COINS = [
    "BTC", "ETH", "SOL", "XRP", "AVAX", "DOGE", "SHIB", "DOT", "UNI",
    "LINK", "LTC", "BCH", "AAVE", "ADA", "ALGO", "ATOM", "CRV", "NEAR",
    "MKR", "GRT", "SUSHI", "YFI", "BAT", "XTZ",
    # Unsupported on Alpaca but included for learning engine diversity
    "BNB", "FTM", "APE", "MATIC",
]

STRATEGY_TYPES = [
    ("scalping",        "1m"),
    ("momentum",        "15m"),
    ("trend_following",  "4h"),
    ("mean_reversion",   "1h"),
    ("breakout",         "30m"),
    ("grid",             "5m"),
    ("swing",            "4h"),
]

# Strategy name templates — maps type to a name suffix
_NAME_SUFFIXES = {
    "scalping":        "Scalper",
    "momentum":        "Momentum",
    "trend_following":  "Trend",
    "mean_reversion":   "MeanRev",
    "breakout":         "Breakout",
    "grid":             "Grid",
    "swing":            "Swing",
}

# Extra timeframe variants for top coins (to push from ~196 → ~200+)
_EXTRA_VARIANTS = [
    {"name": "BTC Scalper 5m",        "type": "scalping",        "timeframe": "5m",  "pair": "BTC/USDT",  "desc": "BTC scalping on 5-minute candles"},
    {"name": "BTC Trend 1h",          "type": "trend_following",  "timeframe": "1h",  "pair": "BTC/USDT",  "desc": "BTC trend following on 1-hour candles"},
    {"name": "ETH Scalper 5m",        "type": "scalping",        "timeframe": "5m",  "pair": "ETH/USDT",  "desc": "ETH scalping on 5-minute candles"},
    {"name": "ETH Breakout 1h",       "type": "breakout",        "timeframe": "1h",  "pair": "ETH/USDT",  "desc": "ETH breakout detection on 1-hour chart"},
    {"name": "SOL Momentum 1h",       "type": "momentum",        "timeframe": "1h",  "pair": "SOL/USDT",  "desc": "SOL momentum strategy on 1-hour candles"},
    {"name": "XRP Swing 1h",          "type": "swing",           "timeframe": "1h",  "pair": "XRP/USDT",  "desc": "XRP swing trading on 1-hour chart"},
]


def _build_strategies():
    """Generate ~200 strategies from all coin × strategy type combinations + extras."""
    strategies = []
    for coin in COINS:
        for stype, tf in STRATEGY_TYPES:
            suffix = _NAME_SUFFIXES[stype]
            name = f"{coin} {suffix} {tf}"
            pair = f"{coin}/USDT"
            desc = f"{coin} {stype.replace('_', ' ')} strategy on {tf} timeframe"
            strategies.append({
                "name": name, "type": stype, "timeframe": tf,
                "pair": pair, "desc": desc,
            })
    # Add extra timeframe variants
    strategies.extend(_EXTRA_VARIANTS)
    return strategies


STRATEGIES = _build_strategies()

# Strategy type → performance characteristics
TYPE_PROFILES = {
    "scalping": {
        "win_rate": (65, 78), "trades_per_day": (50, 120),
        "avg_win": (3, 15), "avg_loss": (2, 10),
        "max_dd": (-8, -3), "sharpe": (0.4, 1.8),
    },
    "momentum": {
        "win_rate": (48, 58), "trades_per_day": (10, 30),
        "avg_win": (20, 60), "avg_loss": (15, 40),
        "max_dd": (-18, -8), "sharpe": (0.2, 1.2),
    },
    "trend_following": {
        "win_rate": (38, 48), "trades_per_day": (3, 10),
        "avg_win": (50, 150), "avg_loss": (20, 50),
        "max_dd": (-22, -10), "sharpe": (0.3, 1.5),
    },
    "mean_reversion": {
        "win_rate": (55, 65), "trades_per_day": (15, 40),
        "avg_win": (15, 40), "avg_loss": (10, 30),
        "max_dd": (-15, -5), "sharpe": (0.5, 1.4),
    },
    "breakout": {
        "win_rate": (42, 52), "trades_per_day": (8, 20),
        "avg_win": (30, 80), "avg_loss": (15, 35),
        "max_dd": (-20, -8), "sharpe": (0.2, 1.1),
    },
    "grid": {
        "win_rate": (68, 82), "trades_per_day": (40, 100),
        "avg_win": (2, 8), "avg_loss": (1, 5),
        "max_dd": (-6, -2), "sharpe": (0.8, 2.0),
    },
    "swing": {
        "win_rate": (40, 50), "trades_per_day": (2, 5),
        "avg_win": (60, 200), "avg_loss": (30, 80),
        "max_dd": (-25, -12), "sharpe": (0.2, 1.3),
    },
}

# Bots that start paused (~5% of total, spread across the list)
PAUSED_BOTS = {4, 10, 14, 22, 35, 48, 63, 77, 91, 105, 119, 133, 147, 161, 175, 189}
# Bots with very few trades (for INSUFFICIENT_DATA testing, ~3%)
LOW_TRADE_BOTS = {14, 17, 55, 82, 110, 140, 170}


def create_tables(conn):
    """Create database schema."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            type TEXT NOT NULL,
            timeframe TEXT,
            pair TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS bots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            strategy_id INTEGER NOT NULL,
            status TEXT DEFAULT 'active',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_active TEXT,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        );

        CREATE TABLE IF NOT EXISTS performance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            win_rate REAL,
            total_trades INTEGER,
            pnl REAL,
            drawdown REAL,
            sharpe_ratio REAL,
            profit_factor REAL,
            avg_win REAL,
            avg_loss REAL,
            consecutive_losses INTEGER,
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER,
            strategy_id INTEGER,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            base_verdict TEXT,
            enhanced_verdict TEXT,
            reasons TEXT,
            adaptation_score REAL,
            regime TEXT,
            executed INTEGER DEFAULT 0,
            FOREIGN KEY (bot_id) REFERENCES bots(id),
            FOREIGN KEY (strategy_id) REFERENCES strategies(id)
        );
    """)
    conn.commit()


def seed_strategies(conn):
    """Insert all strategies (~200)."""
    for s in STRATEGIES:
        try:
            conn.execute(
                "INSERT INTO strategies (name, description, type, timeframe, pair) VALUES (?, ?, ?, ?, ?)",
                (s["name"], s["desc"], s["type"], s["timeframe"], s["pair"])
            )
        except sqlite3.IntegrityError:
            pass  # already exists
    conn.commit()
    print(f"  {G}✓{X} Seeded {len(STRATEGIES)} strategies")


def seed_bots(conn):
    """Insert bots linked to strategies."""
    cursor = conn.execute("SELECT id, name FROM strategies ORDER BY id")
    strategies = cursor.fetchall()

    now = datetime.datetime.utcnow()
    for idx, (strat_id, strat_name) in enumerate(strategies):
        bot_name = f"Bot-{strat_name}"
        status = "paused" if idx in PAUSED_BOTS else "active"
        last_active = (now - datetime.timedelta(hours=random.randint(1, 48))).isoformat()
        try:
            conn.execute(
                "INSERT INTO bots (name, strategy_id, status, last_active) VALUES (?, ?, ?, ?)",
                (bot_name, strat_id, status, last_active)
            )
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
    paused = conn.execute("SELECT COUNT(*) FROM bots WHERE status='paused'").fetchone()[0]
    print(f"  {G}✓{X} Seeded {count} bots ({paused} paused)")


def generate_performance_history(conn):
    """Generate 30 days of realistic performance data per strategy."""
    cursor = conn.execute("SELECT id, name, type FROM strategies ORDER BY id")
    strategies = cursor.fetchall()
    now = datetime.datetime.utcnow().date()
    total_rows = 0

    for idx, (strat_id, strat_name, strat_type) in enumerate(strategies):
        profile = TYPE_PROFILES.get(strat_type, TYPE_PROFILES["momentum"])
        random.seed(hash(strat_name) + 42)

        # Base characteristics for this strategy
        base_wr = random.uniform(*profile["win_rate"])
        base_trades = random.uniform(*profile["trades_per_day"])
        base_avg_win = random.uniform(*profile["avg_win"])
        base_avg_loss = random.uniform(*profile["avg_loss"])
        base_dd = random.uniform(*profile["max_dd"])
        base_sharpe = random.uniform(*profile["sharpe"])

        # Low-trade bots get very few trades
        if idx in LOW_TRADE_BOTS:
            base_trades = random.uniform(0.2, 0.5)

        cumulative_pnl = 0
        rows = []

        for day in range(30):
            date = (now - datetime.timedelta(days=30 - day)).isoformat()

            # Add daily variance
            daily_wr = max(10, min(95, base_wr + random.gauss(0, 3)))
            daily_trades = max(1, int(base_trades + random.gauss(0, base_trades * 0.2)))
            daily_avg_win = max(0.5, base_avg_win + random.gauss(0, base_avg_win * 0.1))
            daily_avg_loss = max(0.5, base_avg_loss + random.gauss(0, base_avg_loss * 0.1))

            # Low trade bots: some days zero trades
            if idx in LOW_TRADE_BOTS and random.random() > 0.3:
                daily_trades = 0

            # Calculate PnL
            wins = int(daily_trades * daily_wr / 100)
            losses = daily_trades - wins
            daily_pnl = (wins * daily_avg_win) - (losses * daily_avg_loss)
            cumulative_pnl += daily_pnl

            # Drawdown oscillates
            dd = base_dd + random.gauss(0, 2)
            dd = max(-40, min(0, dd))

            # Sharpe with variance
            sharpe = base_sharpe + random.gauss(0, 0.15)

            # Profit factor
            if losses * daily_avg_loss > 0:
                pf = (wins * daily_avg_win) / (losses * daily_avg_loss)
            else:
                pf = 2.0

            # Consecutive losses
            consec = 0
            for _ in range(daily_trades):
                if random.random() > (daily_wr / 100):
                    consec += 1
                else:
                    consec = 0

            rows.append((
                strat_id, date, round(daily_wr, 2), daily_trades,
                round(daily_pnl, 2), round(dd, 2), round(sharpe, 3),
                round(pf, 3), round(daily_avg_win, 2), round(daily_avg_loss, 2),
                consec
            ))

        conn.executemany(
            """INSERT INTO performance_history
               (strategy_id, date, win_rate, total_trades, pnl, drawdown,
                sharpe_ratio, profit_factor, avg_win, avg_loss, consecutive_losses)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows
        )
        total_rows += len(rows)

    conn.commit()
    print(f"  {G}✓{X} Generated {total_rows} performance history rows (30 days × {len(strategies)} strategies)")


def verify_data(conn):
    """Print summary of seeded data."""
    print(f"\n  {C}{B}Verification:{X}")
    for table in ["strategies", "bots", "performance_history", "decisions"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {table}: {B}{count}{X} rows")

    # Show strategy types distribution
    cursor = conn.execute("SELECT type, COUNT(*) FROM strategies GROUP BY type ORDER BY COUNT(*) DESC")
    print(f"\n  {C}Strategy types:{X}")
    for stype, count in cursor.fetchall():
        print(f"    {stype}: {count}")

    # Show bot statuses
    cursor = conn.execute("SELECT status, COUNT(*) FROM bots GROUP BY status")
    print(f"\n  {C}Bot statuses:{X}")
    for status, count in cursor.fetchall():
        color = G if status == "active" else Y if status == "paused" else R
        print(f"    {color}{status}: {count}{X}")

    # Show low-trade strategies
    cursor = conn.execute("""
        SELECT s.name, SUM(ph.total_trades) as total
        FROM strategies s
        JOIN performance_history ph ON s.id = ph.strategy_id
        GROUP BY s.id ORDER BY total ASC LIMIT 3
    """)
    print(f"\n  {C}Lowest trade counts (for INSUFFICIENT_DATA testing):{X}")
    for name, total in cursor.fetchall():
        print(f"    {D}{name}: {total} total trades{X}")


def main():
    print(f"\n{C}{B}{'=' * 56}")
    print("  Strategy Factory — Database Seeder")
    print(f"{'=' * 56}{X}\n")

    # Ensure data directory exists
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    os.makedirs(config.REPORT_DIR, exist_ok=True)

    # Check if DB already exists
    if os.path.exists(config.DB_PATH):
        print(f"  {Y}Database already exists at: {config.DB_PATH}{X}")
        response = input(f"  {Y}Reset and reseed? (y/N): {X}").strip().lower()
        if response != "y":
            print(f"  {D}Aborted.{X}")
            return
        os.remove(config.DB_PATH)
        print(f"  {D}Old database removed.{X}")

    # Connect and seed
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    print(f"\n  {C}Creating tables...{X}")
    create_tables(conn)

    print(f"  {C}Seeding strategies...{X}")
    seed_strategies(conn)

    print(f"  {C}Seeding bots...{X}")
    seed_bots(conn)

    print(f"  {C}Generating performance history...{X}")
    generate_performance_history(conn)

    verify_data(conn)
    conn.close()

    print(f"\n  {G}{B}Database ready!{X} {D}{config.DB_PATH}{X}")
    print(f"  {C}Next: python discover_api.py{X}")
    print(f"  {C}Then: python daily_runner.py{X}\n")


if __name__ == "__main__":
    main()
