"""
Strategy Factory Bot Manager — Database Seeder
Initializes SQLite database and populates with bots and strategies
for the professional 1H trading desk universe.

NOTE: Performance history is PLACEHOLDER data for database initialization only.
Real performance comes from live trading via Alpaca.

Usage: python seed_data.py
"""

import os
import sys
import sqlite3
import random
import datetime
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# ── Color helpers ────────────────────────────────────────────────────
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; X = "\033[0m"; D = "\033[90m"


# ── Assets — 10 professional 1H bots per asset ───────────────────────
COINS = config.CRYPTO_ASSETS
STOCKS = config.STOCK_ASSETS

STRATEGY_TYPES = [
    (stype, config.DESK_ENTRY_TIMEFRAME)
    for stype in config.PROFESSIONAL_STRATEGIES
]

_NAME_SUFFIXES = {
    "trend_pullback": "Trend Pullback",
    "ema_crossover": "EMA-X",
    "macd_momentum": "MACD Momentum",
    "rsi_mean_reversion": "RSI-MR",
    "bollinger_reversion": "Bollinger",
    "breakout_retest": "Breakout Retest",
    "donchian_breakout": "Donchian",
    "vwap_bounce": "VWAP",
    "atr_momentum_expansion": "ATR Momentum",
    "supertrend_continuation": "Supertrend",
}

_EXTRA_VARIANTS = []


def _build_strategies():
    """Generate professional 1H strategies for all assets."""
    strategies = []
    for coin in COINS:
        for stype, tf in STRATEGY_TYPES:
            suffix = _NAME_SUFFIXES[stype]
            name = f"{coin} {suffix} {tf}"
            pair = f"{coin}/USDT"
            desc = f"{coin} {suffix} professional strategy on {tf} candles"
            strategies.append({
                "name": name, "type": stype, "timeframe": tf,
                "pair": pair, "desc": desc,
            })
    for symbol in STOCKS:
        for stype, tf in STRATEGY_TYPES:
            suffix = _NAME_SUFFIXES[stype]
            name = f"{symbol} {suffix} {tf}"
            desc = f"{symbol} {suffix} professional strategy on {tf} candles"
            strategies.append({
                "name": name, "type": stype, "timeframe": tf,
                "pair": symbol, "desc": desc,
            })
    strategies.extend(_EXTRA_VARIANTS)
    return strategies


STRATEGIES = _build_strategies()

# Performance profile for placeholder data
# NOTE: This is PLACEHOLDER data for database initialization.
# Real performance comes from live trading.
TYPE_PROFILES = {
    "trend_pullback": {"win_rate": (46, 58), "trades_per_day": (0.5, 2), "avg_win": (25, 70), "avg_loss": (15, 40), "max_dd": (-22, -8), "sharpe": (0.4, 1.2)},
    "ema_crossover": {"win_rate": (42, 55), "trades_per_day": (0.5, 2), "avg_win": (25, 70), "avg_loss": (15, 40), "max_dd": (-28, -10), "sharpe": (0.3, 1.0)},
    "macd_momentum": {"win_rate": (43, 56), "trades_per_day": (0.5, 2), "avg_win": (25, 75), "avg_loss": (15, 42), "max_dd": (-26, -9), "sharpe": (0.3, 1.1)},
    "rsi_mean_reversion": {
        "win_rate": (45, 60), "trades_per_day": (0.5, 2),
        "avg_win": (15, 45), "avg_loss": (10, 30),
        "max_dd": (-20, -8), "sharpe": (0.4, 1.2),
    },
    "bollinger_reversion": {"win_rate": (45, 61), "trades_per_day": (0.5, 2), "avg_win": (15, 42), "avg_loss": (10, 28), "max_dd": (-20, -7), "sharpe": (0.4, 1.2)},
    "breakout_retest": {"win_rate": (40, 54), "trades_per_day": (0.4, 1.5), "avg_win": (30, 85), "avg_loss": (15, 42), "max_dd": (-30, -10), "sharpe": (0.2, 1.0)},
    "donchian_breakout": {"win_rate": (36, 50), "trades_per_day": (0.3, 1.2), "avg_win": (35, 95), "avg_loss": (16, 45), "max_dd": (-35, -12), "sharpe": (0.2, 1.0)},
    "vwap_bounce": {
        "win_rate": (48, 63), "trades_per_day": (0.5, 2),
        "avg_win": (12, 38), "avg_loss": (8, 24),
        "max_dd": (-15, -5), "sharpe": (0.5, 1.3),
    },
    "atr_momentum_expansion": {"win_rate": (38, 52), "trades_per_day": (0.4, 1.4), "avg_win": (35, 95), "avg_loss": (16, 45), "max_dd": (-35, -12), "sharpe": (0.2, 1.0)},
    "supertrend_continuation": {"win_rate": (44, 57), "trades_per_day": (0.5, 2), "avg_win": (25, 75), "avg_loss": (14, 40), "max_dd": (-26, -9), "sharpe": (0.3, 1.1)},
}

PAUSED_BOTS = set()
LOW_TRADE_BOTS = set()


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
    """Insert all strategies."""
    for s in STRATEGIES:
        try:
            conn.execute(
                "INSERT INTO strategies (name, description, type, timeframe, pair) VALUES (?, ?, ?, ?, ?)",
                (s["name"], s["desc"], s["type"], s["timeframe"], s["pair"])
            )
        except sqlite3.IntegrityError:
            pass
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


def generate_performance_history(conn, strategy_ids=None):
    """Generate 30 days of PLACEHOLDER performance data per strategy.
    This is NOT real backtest data — it's for database initialization only.
    Real performance comes from live trading via Alpaca."""
    if strategy_ids:
        placeholders = ",".join("?" for _ in strategy_ids)
        cursor = conn.execute(
            f"SELECT id, name, type FROM strategies WHERE id IN ({placeholders}) ORDER BY id",
            list(strategy_ids),
        )
    else:
        cursor = conn.execute("SELECT id, name, type FROM strategies ORDER BY id")
    strategies = cursor.fetchall()
    now = datetime.datetime.utcnow().date()
    total_rows = 0

    for idx, (strat_id, strat_name, strat_type) in enumerate(strategies):
        profile = TYPE_PROFILES.get(strat_type, TYPE_PROFILES["trend_pullback"])
        random.seed(hash(strat_name) + 42)

        base_wr = random.uniform(*profile["win_rate"])
        base_trades = random.uniform(*profile["trades_per_day"])
        base_avg_win = random.uniform(*profile["avg_win"])
        base_avg_loss = random.uniform(*profile["avg_loss"])
        base_dd = random.uniform(*profile["max_dd"])
        base_sharpe = random.uniform(*profile["sharpe"])

        if idx in LOW_TRADE_BOTS:
            base_trades = random.uniform(0.2, 0.5)

        cumulative_pnl = 0
        rows = []

        for day in range(30):
            date = (now - datetime.timedelta(days=30 - day)).isoformat()

            daily_wr = max(10, min(95, base_wr + random.gauss(0, 3)))
            daily_trades = max(1, int(base_trades + random.gauss(0, base_trades * 0.2)))
            daily_avg_win = max(0.5, base_avg_win + random.gauss(0, base_avg_win * 0.1))
            daily_avg_loss = max(0.5, base_avg_loss + random.gauss(0, base_avg_loss * 0.1))

            if idx in LOW_TRADE_BOTS and random.random() > 0.3:
                daily_trades = 0

            wins = int(daily_trades * daily_wr / 100)
            losses = daily_trades - wins
            daily_pnl = (wins * daily_avg_win) - (losses * daily_avg_loss)
            cumulative_pnl += daily_pnl

            dd = base_dd + random.gauss(0, 2)
            dd = max(-40, min(0, dd))
            sharpe = base_sharpe + random.gauss(0, 0.15)

            if losses * daily_avg_loss > 0:
                pf = (wins * daily_avg_win) / (losses * daily_avg_loss)
            else:
                pf = 2.0

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
    print(f"  {G}✓{X} Generated {total_rows} PLACEHOLDER performance rows (30 days x {len(strategies)} strategies)")
    print(f"  {Y}⚠ This is placeholder data for DB init — real performance comes from live trading{X}")


def verify_data(conn):
    """Print summary of seeded data."""
    print(f"\n  {C}{B}Verification:{X}")
    for table in ["strategies", "bots", "performance_history", "decisions"]:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"    {table}: {B}{count}{X} rows")

    cursor = conn.execute("SELECT type, COUNT(*) FROM strategies GROUP BY type ORDER BY COUNT(*) DESC")
    print(f"\n  {C}Strategy types:{X}")
    for stype, count in cursor.fetchall():
        print(f"    {stype}: {count}")

    cursor = conn.execute("SELECT status, COUNT(*) FROM bots GROUP BY status")
    print(f"\n  {C}Bot statuses:{X}")
    for status, count in cursor.fetchall():
        color = G if status == "active" else Y if status == "paused" else R
        print(f"    {color}{status}: {count}{X}")

    # Show asset breakdown
    cursor = conn.execute("SELECT pair, name FROM strategies ORDER BY pair")
    print(f"\n  {C}Assets:{X}")
    for pair, name in cursor.fetchall():
        print(f"    {D}{pair}: {name}{X}")


def ensure_seed_data():
    """Non-destructively ensure the professional 1H bot universe exists."""
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    create_tables(conn)
    before = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    seed_strategies(conn)
    seed_bots(conn)
    after = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]
    missing_perf = conn.execute("""
        SELECT s.id
        FROM strategies s
        LEFT JOIN performance_history p ON p.strategy_id = s.id
        GROUP BY s.id
        HAVING COUNT(p.id) = 0
    """).fetchall()
    if missing_perf:
        generate_performance_history(conn, strategy_ids=[row[0] for row in missing_perf])
    conn.close()
    return {"strategies_before": before, "strategies_after": after, "added": max(0, after - before)}


def main():
    print(f"\n{C}{B}{'=' * 56}")
    print("  Strategy Factory — Database Seeder (Professional 1H Desk)")
    print(f"{'=' * 56}{X}\n")

    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    os.makedirs(config.REPORT_DIR, exist_ok=True)

    if os.path.exists(config.DB_PATH):
        print(f"  {Y}Database already exists at: {config.DB_PATH}{X}")
        response = input(f"  {Y}Reset and reseed? (y/N): {X}").strip().lower()
        if response != "y":
            print(f"  {D}Aborted.{X}")
            return
        os.remove(config.DB_PATH)
        print(f"  {D}Old database removed.{X}")

    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    print(f"\n  {C}Creating tables...{X}")
    create_tables(conn)

    print(f"  {C}Seeding strategies...{X}")
    seed_strategies(conn)

    print(f"  {C}Seeding bots...{X}")
    seed_bots(conn)

    print(f"  {C}Generating placeholder performance history...{X}")
    generate_performance_history(conn)

    verify_data(conn)
    conn.close()

    n_assets = len(COINS) + len(STOCKS)
    n_bots = len(STRATEGIES)
    print(f"\n  {G}{B}Database ready!{X} {D}{config.DB_PATH}{X}")
    print(f"  {C}Assets: {len(COINS)} crypto + {len(STOCKS)} stocks = {n_assets} total{X}")
    print(f"  {C}Bots: {n_bots} professional 1H bots ({len(config.PROFESSIONAL_STRATEGIES)} per asset){X}")
    print(f"  {C}Strategies: {', '.join(config.PROFESSIONAL_STRATEGIES)}{X}")
    print(f"  {C}Next: python daily_runner.py{X}\n")


if __name__ == "__main__":
    main()
