"""
Strategy Factory Bot Manager — API & System Discovery Tool
Run this first to verify everything is set up correctly.

Usage: python discover_api.py
"""

import os
import sys
import json
import sqlite3
import importlib
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── ANSI Colors ──────────────────────────────────────────────────────
G = "\033[92m"  # green
R = "\033[91m"  # red
Y = "\033[93m"  # yellow
C = "\033[96m"  # cyan
W = "\033[97m"  # white
D = "\033[90m"  # dim
B = "\033[1m"   # bold
X = "\033[0m"   # reset

PASS = f"  {G}✓ PASS{X}"
FAIL = f"  {R}✗ FAIL{X}"
WARN = f"  {Y}⚠ WARN{X}"
INFO = f"  {C}ℹ INFO{X}"


def check(name, fn):
    """Run a check function and print pass/fail."""
    try:
        result = fn()
        if result is True:
            print(f"{PASS}  {name}")
            return True
        elif result is False:
            print(f"{FAIL}  {name}")
            return False
        else:
            print(f"{PASS}  {name} — {D}{result}{X}")
            return True
    except Exception as e:
        print(f"{FAIL}  {name} — {R}{e}{X}")
        return False


def main():
    print(f"\n{C}{B}{'=' * 56}")
    print("  Strategy Factory — System Discovery & Diagnostics")
    print(f"{'=' * 56}{X}\n")

    passed = 0
    failed = 0
    total = 0

    # ── 1. Check Python version ──────────────────────────────────────
    def check_python():
        v = sys.version_info
        if v.major >= 3 and v.minor >= 8:
            return f"Python {v.major}.{v.minor}.{v.micro}"
        return False

    total += 1
    if check("Python version >= 3.8", check_python):
        passed += 1
    else:
        failed += 1

    # ── 2. Check required packages ───────────────────────────────────
    packages = ["requests", "json", "sqlite3", "os", "datetime", "logging", "argparse"]
    optional_packages = ["numpy"]

    for pkg in packages:
        total += 1
        if check(f"Import: {pkg}", lambda p=pkg: importlib.import_module(p) and True):
            passed += 1
        else:
            failed += 1

    for pkg in optional_packages:
        total += 1
        try:
            importlib.import_module(pkg)
            print(f"{PASS}  Import: {pkg} (optional)")
            passed += 1
        except ImportError:
            print(f"{WARN}  Import: {pkg} (optional, some features degraded)")
            passed += 1  # warn, not fail

    # ── 3. Check config module ───────────────────────────────────────
    total += 1
    if check("Import: config", lambda: importlib.import_module("config") and True):
        passed += 1
        import config as cfg

        total += 1
        if check("Config: DB_PATH defined", lambda: bool(cfg.DB_PATH)):
            passed += 1
        else:
            failed += 1

        total += 1
        if check("Config: REPORT_DIR defined", lambda: bool(cfg.REPORT_DIR)):
            passed += 1
        else:
            failed += 1
    else:
        failed += 1

    # ── 4. Check project modules ─────────────────────────────────────
    modules = [
        ("api_client", "StrategyFactoryClient"),
        ("analytics", "StrategyMetrics"),
        ("decision_engine", "evaluate_bot"),
        ("learning_engine", "LearningEngine"),
        ("generate_dashboard", "DashboardGenerator"),
    ]
    for mod_name, cls_name in modules:
        total += 1
        def _check(m=mod_name, c=cls_name):
            mod = importlib.import_module(m)
            obj = getattr(mod, c)
            return f"found {c}"
        if check(f"Module: {mod_name}.{cls_name}", _check):
            passed += 1
        else:
            failed += 1

    # ── 5. Check database ────────────────────────────────────────────
    total += 1
    import config as cfg
    db_exists = os.path.exists(cfg.DB_PATH)
    if db_exists:
        print(f"{PASS}  Database file exists: {D}{cfg.DB_PATH}{X}")
        passed += 1

        # Check tables
        try:
            conn = sqlite3.connect(cfg.DB_PATH)
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            total += 1
            if "strategies" in tables and "bots" in tables:
                print(f"{PASS}  Database tables: {D}{', '.join(tables)}{X}")
                passed += 1
            else:
                print(f"{FAIL}  Database missing required tables (found: {tables})")
                failed += 1

            # Check bot count
            conn = sqlite3.connect(cfg.DB_PATH)
            cursor = conn.execute("SELECT COUNT(*) FROM bots")
            count = cursor.fetchone()[0]
            conn.close()
            total += 1
            if count > 0:
                print(f"{PASS}  Bots in database: {D}{count}{X}")
                passed += 1
            else:
                print(f"{WARN}  No bots in database — run: python seed_data.py")
                failed += 1
        except Exception as e:
            total += 1
            print(f"{FAIL}  Database query error: {e}")
            failed += 1
    else:
        print(f"{WARN}  Database not found — run: python seed_data.py")
        failed += 1

    # ── 6. Check reports directory ───────────────────────────────────
    total += 1
    if os.path.isdir(cfg.REPORT_DIR):
        print(f"{PASS}  Reports directory exists: {D}{cfg.REPORT_DIR}{X}")
        passed += 1
    else:
        try:
            os.makedirs(cfg.REPORT_DIR, exist_ok=True)
            print(f"{PASS}  Reports directory created: {D}{cfg.REPORT_DIR}{X}")
            passed += 1
        except Exception as e:
            print(f"{FAIL}  Cannot create reports dir: {e}")
            failed += 1

    # ── 7. Test Binance public API ───────────────────────────────────
    total += 1
    def check_binance():
        import requests
        r = requests.get(f"{cfg.BINANCE_BASE_URL}/api/v3/ping", timeout=10)
        if r.status_code == 200:
            return "Binance API reachable"
        return False

    if check("Binance public API (ping)", check_binance):
        passed += 1
    else:
        print(f"  {D}  (Binance API not reachable — market data features will use cached/mock data){X}")
        failed += 1

    total += 1
    def check_binance_data():
        import requests
        r = requests.get(f"{cfg.BINANCE_BASE_URL}/api/v3/ticker/24hr",
                        params={"symbol": "BTCUSDT"}, timeout=10)
        data = r.json()
        price = data.get("lastPrice", "?")
        return f"BTC/USDT = ${float(price):,.2f}"

    if check("Binance market data (BTCUSDT)", check_binance_data):
        passed += 1
    else:
        failed += 1

    # ── 8. Test mock dashboard generation ────────────────────────────
    total += 1
    def check_dashboard():
        from generate_dashboard import DashboardGenerator
        gen = DashboardGenerator()
        html = gen.generate_mock()
        if len(html) > 1000 and "<html" in html:
            return f"Generated {len(html):,} chars"
        return False

    if check("Mock dashboard generation", check_dashboard):
        passed += 1
    else:
        failed += 1

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{C}{B}{'─' * 56}{X}")
    pct = (passed / total * 100) if total > 0 else 0
    color = G if failed == 0 else Y if failed <= 2 else R
    print(f"  {color}{B}Results: {passed}/{total} passed ({pct:.0f}%){X}")
    if failed == 0:
        print(f"  {G}All systems operational! Run: python daily_runner.py{X}")
    elif failed <= 2:
        print(f"  {Y}Minor issues. System should still work.{X}")
    else:
        print(f"  {R}Multiple failures. Check errors above.{X}")

    if not db_exists:
        print(f"\n  {Y}Next step: python seed_data.py{X}")
    print(f"{C}{B}{'─' * 56}{X}\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
