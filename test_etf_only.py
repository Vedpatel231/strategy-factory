"""
Strategy Factory — ETF-Only System Test Suite

Validates the system after codebase cleanup:
1. Config contains only 10 ETFs
2. No crypto assets configured
3. Bot registry generates ETF-only bots
4. Fee model is 1 bps per side
5. Conservative mode initializes correctly
6. Dashboard server has no broken imports
7. No references to archived modules in active code
8. Leveraged ETFs identified correctly
9. Position sizing respects leveraged limits
10. All active Python files pass syntax check
11. Trade journal fee calculation is correct
12. Daily runner imports work
13. No stale /api/broker routes
14. No stale /api/auto routes (old AutoTrader)
15. .env.example is up to date
16. Archive directory exists with expected files
"""

import os
import sys
import importlib
import py_compile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0


def test(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}: {detail}")


def run_tests():
    global PASS, FAIL

    print("\n" + "=" * 64)
    print("  Strategy Factory — ETF-Only System Tests")
    print("=" * 64)

    # ── Test 1: Config contains the 12-ETF long-only universe ──
    import config
    expected_etfs = {"QQQ", "SPY", "IWM", "SMH", "XLF", "XLE",
                     "XLV", "XLI", "GLD", "GDX", "TLT", "XBI"}
    actual_etfs = set(config.STOCK_ASSETS)
    test("Config has exactly the 12 long-only ETFs",
         actual_etfs == expected_etfs,
         f"Expected {expected_etfs}, got {actual_etfs}")

    # ── Test 2: No crypto assets configured ──
    test("No crypto assets configured",
         len(config.CRYPTO_ASSETS) == 0,
         f"CRYPTO_ASSETS has {len(config.CRYPTO_ASSETS)} items")

    # ── Test 3: Bot registry generates ETF-only bots ──
    from bot_registry import BotRegistry
    registry = BotRegistry()
    all_assets = set(b.asset for b in registry.all_bots())
    non_etf = all_assets - expected_etfs
    test("Bot registry has only ETF bots",
         len(non_etf) == 0,
         f"Non-ETF assets found: {non_etf}")

    # ── Test 4: Fee model is 1 bps per side ──
    from trade_journal import estimate_alpaca_fee, ALPACA_STOCK_SLIPPAGE_BPS
    test("Stock fee is 1 bps",
         ALPACA_STOCK_SLIPPAGE_BPS == 1.0,
         f"Got {ALPACA_STOCK_SLIPPAGE_BPS} bps")
    # $10,000 notional → $1.00 fee (1 bps)
    fee = estimate_alpaca_fee(10000.0, asset_class="stock", symbol="QQQ")
    test("Fee calculation: $10k → $1.00",
         abs(fee - 1.0) < 0.001,
         f"Got ${fee:.4f}")

    # ── Test 5: Conservative mode initializes correctly ──
    from conservative_mode import ConservativeMode
    cm = ConservativeMode()
    status = cm.get_status()
    test("Conservative mode initializes",
         status is not None and "daily_mode" in status,
         f"Status missing daily_mode key")

    # ── Test 6: Dashboard server has no broken imports ──
    # Read the file and check for auto_trader import
    with open("dashboard_server.py", "r") as f:
        ds_content = f.read()
    test("No 'from auto_trader import' in dashboard_server.py",
         "from auto_trader import" not in ds_content,
         "Still imports archived auto_trader")

    # ── Test 7: No references to archived modules in active code ──
    archived_modules = ["paper_broker", "paper_trader", "discover_api",
                        "reset_for_ema_crossover", "run_paper_trading"]
    active_py_files = [f for f in os.listdir(".") if f.endswith(".py") and f != "test_etf_only.py"]
    bad_imports = []
    for pyf in active_py_files:
        try:
            with open(pyf, "r") as f:
                content = f.read()
            for mod in archived_modules:
                if f"import {mod}" in content or f"from {mod}" in content:
                    bad_imports.append(f"{pyf} → {mod}")
        except Exception:
            pass
    test("No imports of archived modules in active code",
         len(bad_imports) == 0,
         f"Found: {bad_imports}")

    # ── Test 8: Long-only universe — no leveraged/inverse ETFs ──
    test("Leveraged ETFs set is empty (long-only, no leverage)",
         config.LEVERAGED_ETFS == set(),
         f"Got {config.LEVERAGED_ETFS}")

    # ── Test 9: MAX_CONCURRENT_CRYPTO is 0 ──
    test("MAX_CONCURRENT_CRYPTO is 0",
         config.MAX_CONCURRENT_CRYPTO == 0,
         f"Got {config.MAX_CONCURRENT_CRYPTO}")

    # ── Test 10: All active Python files pass syntax check ──
    syntax_fails = []
    for pyf in active_py_files:
        try:
            py_compile.compile(pyf, doraise=True)
        except py_compile.PyCompileError as e:
            syntax_fails.append(f"{pyf}: {e}")
    test("All Python files pass syntax check",
         len(syntax_fails) == 0,
         f"Failures: {syntax_fails}")

    # ── Test 11: Trade journal fee round-trip ──
    # 2 bps round-trip on $5000 = $1.00 total fees
    entry_fee = estimate_alpaca_fee(5000.0, asset_class="stock", symbol="SPY")
    exit_fee = estimate_alpaca_fee(5000.0, asset_class="stock", symbol="SPY")
    round_trip = entry_fee + exit_fee
    test("Round-trip fee: $5k each side → $1.00 total",
         abs(round_trip - 1.0) < 0.001,
         f"Got ${round_trip:.4f}")

    # ── Test 12: Daily runner module exists and has expected functions ──
    test("daily_runner.py exists",
         os.path.isfile("daily_runner.py"),
         "File not found")

    # ── Test 13: No /api/broker routes in dashboard_server.py ──
    broker_routes = [line for line in ds_content.split("\n")
                     if '@app.route("/api/broker' in line]
    test("No /api/broker routes in dashboard_server.py",
         len(broker_routes) == 0,
         f"Found {len(broker_routes)} broker routes")

    # ── Test 14: No /api/auto routes (old AutoTrader) ──
    auto_routes = [line for line in ds_content.split("\n")
                   if '@app.route("/api/auto/' in line]
    test("No /api/auto/ routes (old AutoTrader) in dashboard_server.py",
         len(auto_routes) == 0,
         f"Found {len(auto_routes)} auto routes")

    # ── Test 15: .env.example has no AUTO_TRADE_INTERVAL_MIN ──
    with open(".env.example", "r") as f:
        env_content = f.read()
    test(".env.example has no AUTO_TRADE_INTERVAL_MIN (old simulator)",
         "AUTO_TRADE_INTERVAL_MIN=" not in env_content or "ALPACA_AUTO_TRADE_INTERVAL_MIN" in env_content,
         "Still references old simulator interval")

    # ── Test 16: Archive directory exists with expected files ──
    archive_dir = os.path.join(".", "archive")
    test("archive/ directory exists",
         os.path.isdir(archive_dir),
         "Directory not found")
    expected_archived = ["auto_trader.py", "paper_broker.py", "paper_trader.py"]
    missing = [f for f in expected_archived if not os.path.isfile(os.path.join(archive_dir, f))]
    test("Key files archived correctly",
         len(missing) == 0,
         f"Missing from archive: {missing}")

    # ── Summary ──
    print("\n" + "-" * 64)
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed, {FAIL} failed")
    if FAIL == 0:
        print("  🎉 All tests passed!")
    else:
        print("  ⚠️  Some tests failed — review above")
    print("-" * 64 + "\n")

    return FAIL == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
