"""
Strategy Factory — Fresh Start Reset for Professional 1H Desk Migration

Clears all old strategy data, trade history, and learning state so the
system starts clean with the new professional 1H trading desk configuration.

Run ONCE after deploying the new code:
    python reset_for_ema_crossover.py

What it does:
  1. Deletes and re-seeds the SQLite database with professional 1H desk bots
  2. Clears the trade journal (old trend_following / backfill_recovery entries)
  3. Clears the learning engine state
  4. Clears the intraday state cache
  5. Clears the trade ledger CSV
  6. Clears the position risk book
  7. Clears the live monitor snapshot
  8. Keeps Alpaca account history (that's on Alpaca's side, not ours)
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; X = "\033[0m"; D = "\033[90m"

DATA_FILES_TO_CLEAR = [
    "trade_journal.json",
    "learning_state.json",
    "intraday_state.json",
    "alpaca_trade_ledger.csv",
    "position_risk_state.json",
    "live_monitor_24h.json",
    "paper_account.json",
    "alpaca_trade_runs.json",
    "last_refresh.json",
]


def main():
    print(f"\n{C}{B}{'=' * 56}")
    print("  Professional 1H Desk Migration — Fresh Start Reset")
    print(f"{'=' * 56}{X}\n")

    data_dir = config.DATA_DIR
    print(f"  Data directory: {D}{data_dir}{X}\n")

    # 1. Clear old data files
    print(f"  {C}Clearing old state files...{X}")
    for filename in DATA_FILES_TO_CLEAR:
        filepath = os.path.join(data_dir, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            print(f"    {G}✓{X} Removed {filename}")
        else:
            print(f"    {D}— {filename} (not found, skip){X}")

    # 2. Delete and re-seed database
    print(f"\n  {C}Re-seeding database...{X}")
    db_path = config.DB_PATH
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"    {G}✓{X} Removed old database")

    # Import and run seed_data
    import seed_data
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    seed_data.create_tables(conn)
    seed_data.seed_strategies(conn)
    seed_data.seed_bots(conn)
    seed_data.generate_performance_history(conn)
    seed_data.verify_data(conn)
    conn.close()

    # 3. Regenerate dashboard
    print(f"\n  {C}Regenerating dashboard...{X}")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "daily_runner.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            print(f"    {G}✓{X} Dashboard regenerated")
        else:
            print(f"    {Y}⚠{X} Dashboard generation had issues (non-fatal)")
            if result.stderr:
                print(f"      {D}{result.stderr[:200]}{X}")
    except Exception as e:
        print(f"    {Y}⚠{X} Dashboard generation skipped: {e}")

    print(f"\n  {G}{B}Fresh start complete!{X}")
    print(f"  {C}New system:{X}")
    print(f"    • 10 professional 1H strategy bots per asset")
    print(f"    • CEO → asset manager → bot hierarchy")
    print(f"    • ATR-based stops, take profits, partials, and trailing stops")
    print(f"    • All old trade history cleared")
    print(f"    • Learning engine reset to zero")
    print(f"\n  {C}Next steps:{X}")
    print(f"    1. Push to Railway: git push")
    print(f"    2. On Railway, run: python reset_for_ema_crossover.py")
    print(f"    3. The auto-trader will start using the new strategy automatically\n")


if __name__ == "__main__":
    main()
