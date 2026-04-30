"""
Strategy Factory — Alpaca Auto-Trader Background Worker

Multi-schedule worker:
  - 4h main cycle: Adaptive Breakout entries (crypto 24/7, stocks during market hours)
  - 15m intraday cycle: RSI-MR, MACD, VWAP, EMA-X for stocks (market hours only)
  - 30m intraday cycle: Same strategies, 30m timeframe (market hours only)
  - 15m exit checks: ATR trailing, hard stop, ADX exit, TP for all positions

Each main cycle:
  1. Invoke daily_runner.py to refresh portfolio analysis
  2. Execute rebalancing trades on Alpaca paper trading

Intraday cycles:
  1. Evaluate intraday strategies for stock symbols
  2. Execute trades for signals with sufficient confidence

Controlled via data/alpaca_auto_trade.enabled flag file.
"""

import os
import json
import time
import logging
import threading
import subprocess
import datetime

import config
from risk_manager import RiskManager

logger = logging.getLogger("alpaca_auto_trader")

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = config.DATA_DIR
REPORT_DIR = config.REPORT_DIR
FLAG_FILE = os.path.join(DATA_DIR, "alpaca_auto_trade.enabled")
LOG_FILE = os.path.join(DATA_DIR, "alpaca_auto_trade.log.json")
# Main cycle runs every 4 hours (matching 4h candle timeframe)
# Exit checks run every 15 minutes for faster stop-loss response
DEFAULT_INTERVAL_MIN = int(
    os.environ.get("ALPACA_AUTO_TRADE_INTERVAL_MIN")
    or os.environ.get("AUTO_TRADE_INTERVAL_MIN")
    or "240"  # 4 hours = 240 minutes
)
EXIT_CHECK_INTERVAL_MIN = int(os.environ.get("EXIT_CHECK_INTERVAL_MIN", "15"))

# Intraday cycle intervals (stocks only, market hours only)
INTRADAY_15M_INTERVAL_SEC = 15 * 60   # 15 minutes
INTRADAY_30M_INTERVAL_SEC = 30 * 60   # 30 minutes


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


class AlpacaAutoTrader:
    """Background thread that refreshes analysis + rebalances on Alpaca every N minutes."""

    _instance = None

    def __init__(self, interval_min=DEFAULT_INTERVAL_MIN):
        self.interval_sec = interval_min * 60
        self.interval_min = interval_min
        self._thread = None
        self._stop = threading.Event()
        self._last_run = None
        self._last_result = None
        self._last_error = None
        self._runs_log = self._load_log()

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = AlpacaAutoTrader()
        return cls._instance

    # ── ENABLED / DISABLED FLAG ──────────────────────────────────────────
    @staticmethod
    def is_enabled():
        return os.path.exists(FLAG_FILE)

    @staticmethod
    def set_enabled(on):
        os.makedirs(DATA_DIR, exist_ok=True)
        if on:
            with open(FLAG_FILE, "w") as f:
                f.write(datetime.datetime.utcnow().isoformat())
        else:
            if os.path.exists(FLAG_FILE):
                os.remove(FLAG_FILE)

    # ── LOG ──────────────────────────────────────────────────────────────
    def _load_log(self):
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _append_log(self, entry):
        self._runs_log.append(entry)
        self._runs_log = self._runs_log[-100:]
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(LOG_FILE, "w") as f:
                json.dump(self._runs_log, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Could not write log: {e}")

    def _refresh_live_monitor(self):
        try:
            from live_monitor import write_live_monitor_snapshot
            write_live_monitor_snapshot(hours=24)
        except Exception as e:
            logger.warning(f"Could not refresh live monitor snapshot: {e}")

    # ── WORKER LOOP ──────────────────────────────────────────────────────
    def start(self):
        if self._thread and self._thread.is_alive():
            logger.info("AlpacaAutoTrader already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="AlpacaAutoTrader")
        self._thread.start()
        logger.info(f"AlpacaAutoTrader thread started (interval {self.interval_min}min)")

    def stop(self):
        self._stop.set()

    def _loop(self):
        logger.info("AlpacaAutoTrader loop entered (4h main + 15m/30m intraday + 15m exit checks)")
        exit_check_sec = EXIT_CHECK_INTERVAL_MIN * 60
        last_main_run = 0
        last_15m_run = 0
        last_30m_run = 0

        while not self._stop.is_set():
            if self.is_enabled():
                now = time.time()
                # Main cycle: run every 4 hours (entry checks + full analysis)
                if now - last_main_run >= self.interval_sec:
                    try:
                        self._run_once()
                        last_main_run = now
                    except Exception as e:
                        self._last_error = str(e)
                        logger.error(f"Alpaca auto run failed: {e}", exc_info=True)
                        self._append_log({
                            "timestamp": datetime.datetime.utcnow().isoformat(),
                            "status": "error",
                            "error": str(e),
                        })
                        last_main_run = now  # don't retry immediately

                # Intraday cycles: stocks only, market hours only
                try:
                    from alpaca_client import is_us_market_open
                    market_open = is_us_market_open()
                except Exception:
                    market_open = False

                if market_open:
                    # 15m intraday cycle
                    if now - last_15m_run >= INTRADAY_15M_INTERVAL_SEC:
                        try:
                            self._run_intraday_cycle("15m")
                            last_15m_run = now
                        except Exception as e:
                            logger.warning(f"Intraday 15m cycle failed: {e}")
                            last_15m_run = now

                    # 30m intraday cycle
                    if now - last_30m_run >= INTRADAY_30M_INTERVAL_SEC:
                        try:
                            self._run_intraday_cycle("30m")
                            last_30m_run = now
                        except Exception as e:
                            logger.warning(f"Intraday 30m cycle failed: {e}")
                            last_30m_run = now

                # Between main/intraday cycles: check exits
                try:
                    self._check_exits_only()
                except Exception as e:
                    logger.warning(f"Exit check failed: {e}")

            # Sleep in 10-second slices for exit_check_interval
            for _ in range(exit_check_sec // 10):
                if self._stop.is_set():
                    return
                time.sleep(10)

    def _run_once(self):
        """One full cycle: re-analyze, then rebalance on Alpaca."""
        start_ts = utc_now()
        logger.info("🦙 Alpaca auto-trade cycle start")

        entry = {
            "timestamp": start_ts.isoformat(),
            "status": "running",
            "broker": "alpaca",
            "steps": {},
        }

        try:
            # Risk checks before any trading
            rm = RiskManager()
            from alpaca_client import AlpacaPaperClient
            client = AlpacaPaperClient()
            acct = client.get_account()
            equity = float(acct.get("equity", 0))

            ok, reasons = rm.pre_trade_check(equity)
            if not ok:
                logger.warning(f"Risk manager blocked trading: {reasons}")
                entry["status"] = "risk_blocked"
                entry["risk_reasons"] = reasons
                self._append_log(entry)
                self._refresh_live_monitor()
                self._last_result = entry
                return

            # Enforce position stop losses before rebalancing
            closed = rm.enforce_position_stops(client)
            if closed:
                logger.info(f"Stop-loss closed {len(closed)} positions: {closed}")
                entry["steps"]["stop_losses"] = closed
        except Exception as e:
            logger.error(f"Risk manager check failed: {e}", exc_info=True)

        # Step 1: Run daily_runner (refresh analysis + dashboard)
        env = dict(os.environ)
        env["SF_TRIGGER"] = "alpaca_auto"
        try:
            result = subprocess.run(
                ["python3", "daily_runner.py"],
                cwd=BASE, capture_output=True, text=True, timeout=240, env=env,
            )
            entry["steps"]["analysis"] = {
                "ok": result.returncode == 0,
                "stdout_tail": result.stdout[-500:],
            }
            if result.returncode != 0:
                entry["status"] = "analysis_failed"
                entry["error"] = result.stderr[-500:]
                self._append_log(entry)
                self._refresh_live_monitor()
                self._last_result = entry
                return
        except subprocess.TimeoutExpired:
            entry["status"] = "timeout"
            self._append_log(entry)
            self._refresh_live_monitor()
            self._last_result = entry
            return

        # Step 2: Load fresh portfolio, execute rebalance on Alpaca
        try:
            portfolio_path = os.path.join(REPORT_DIR, "latest_portfolio.json")
            with open(portfolio_path) as f:
                portfolio = json.load(f)

            from alpaca_trader import AlpacaTrader
            trader = AlpacaTrader()
            results = trader.execute_portfolio(portfolio, dry_run=False)
            acct = trader.client.get_account()

            entry["steps"]["trade"] = {
                "ok": True,
                "summary": results.get("summary", {}),
                "equity_after": acct["equity"],
            }
            entry["status"] = "ok"
        except Exception as e:
            entry["steps"]["trade"] = {"ok": False, "error": str(e)}
            entry["status"] = "trade_failed"
            entry["error"] = str(e)

        entry["duration_sec"] = (utc_now() - start_ts).total_seconds()
        self._last_run = start_ts.isoformat()
        self._last_result = entry
        self._last_error = None
        self._append_log(entry)
        self._refresh_live_monitor()
        logger.info(f"🦙 Alpaca auto-trade cycle complete ({entry['status']})")

    def _run_intraday_cycle(self, timeframe):
        """Evaluate intraday strategies for stock symbols and execute trades."""
        from alpaca_client import AlpacaPaperClient, is_equity_symbol, normalize_crypto_symbol
        from intraday_engine import IntradaySignalEngine, INTRADAY_STRATEGY_INSTANCES
        from risk_manager import RiskManager

        logger.info(f"📊 Intraday {timeframe} cycle start")
        start_ts = utc_now()

        client = AlpacaPaperClient()
        engine = IntradaySignalEngine()
        rm = RiskManager()

        acct = client.get_account()
        equity = float(acct.get("equity", 0))
        buying_power = float(acct.get("buying_power", 0))

        # Load current portfolio allocations for sizing
        portfolio_path = os.path.join(REPORT_DIR, "latest_portfolio.json")
        allocations = []
        if os.path.exists(portfolio_path):
            try:
                with open(portfolio_path) as f:
                    allocations = json.load(f).get("allocations", [])
            except Exception:
                pass

        # Current positions — avoid doubling up
        positions_list = client.get_positions()
        held_symbols = {normalize_crypto_symbol(p["symbol"]) for p in positions_list}

        # Count current stock positions for concurrency limit
        stock_count = sum(1 for p in positions_list if is_equity_symbol(p["symbol"]))

        stock_symbols = config.STOCK_ASSETS
        intraday_strategies = list(INTRADAY_STRATEGY_INSTANCES.keys())

        signals_found = 0
        trades_executed = 0

        for symbol in stock_symbols:
            # Skip if already holding this symbol
            if symbol in held_symbols:
                continue
            # Concurrency limit for intraday stocks
            if stock_count >= config.MAX_CONCURRENT_INTRADAY_STOCKS:
                logger.debug(f"Max {config.MAX_CONCURRENT_INTRADAY_STOCKS} intraday stock positions — skipping")
                break

            for strategy_name in intraday_strategies:
                try:
                    result = engine.evaluate_symbol_intraday(symbol, strategy_name, timeframe)
                    action = result.get("action", "hold")
                    confidence = float(result.get("confidence", 0))
                    accepted = result.get("accepted", False)

                    if action != "buy" or not accepted or confidence < 0.60:
                        continue

                    signals_found += 1

                    # Find matching allocation for sizing
                    alloc_usd = 0
                    for a in allocations:
                        if a.get("strategy_type") == strategy_name and symbol in a.get("pair", ""):
                            alloc_usd = a.get("allocation_usd", 0)
                            break

                    if alloc_usd < 10:
                        logger.debug(f"  {strategy_name} {timeframe} {symbol}: no allocation")
                        continue

                    # Risk checks
                    try:
                        if not rm.can_place_order(symbol):
                            continue
                        if not rm.can_submit_order(symbol, "buy"):
                            continue
                    except Exception:
                        pass

                    order_usd = min(alloc_usd, buying_power * 0.9)  # leave 10% buffer
                    if order_usd < 5:
                        continue

                    order_result = client.submit_order(symbol, order_usd, side="buy")
                    trades_executed += 1
                    stock_count += 1
                    buying_power -= order_usd
                    held_symbols.add(symbol)

                    try:
                        rm.record_order(symbol)
                        rm.record_submitted_order(symbol, "buy")
                    except Exception:
                        pass

                    logger.info(
                        f"  ✅ {strategy_name} {timeframe} BUY {symbol} "
                        f"${order_usd:.2f} (confidence={confidence:.0%})"
                    )
                    break  # one strategy per symbol per cycle

                except Exception as e:
                    logger.debug(f"  Eval/trade error {strategy_name}/{timeframe}/{symbol}: {e}")

        duration = (utc_now() - start_ts).total_seconds()
        logger.info(
            f"📊 Intraday {timeframe} cycle done: "
            f"{signals_found} signals, {trades_executed} trades ({duration:.1f}s)"
        )

        self._append_log({
            "timestamp": start_ts.isoformat(),
            "status": "ok",
            "cycle_type": f"intraday_{timeframe}",
            "signals": signals_found,
            "trades": trades_executed,
            "duration_sec": duration,
        })

        if trades_executed > 0:
            self._refresh_live_monitor()

    def _check_exits_only(self):
        """Quick exit check between main cycles — ATR trailing, hard stop, ADX exit."""
        try:
            from alpaca_client import AlpacaPaperClient
            from alpaca_client import normalize_crypto_symbol
            from alpaca_trader import AlpacaTrader

            client = AlpacaPaperClient()
            positions_list = client.get_positions()
            if not positions_list:
                return  # no open positions, nothing to check

            positions = {normalize_crypto_symbol(p["symbol"]): p for p in positions_list}
            trader = AlpacaTrader()

            # Backfill risk book if needed
            trader._backfill_risk_book(positions)

            exit_orders = trader._enforce_adaptive_exits(positions)
            if exit_orders:
                logger.info(f"Exit check closed {len(exit_orders)} positions")
                self._refresh_live_monitor()
        except Exception as e:
            logger.debug(f"Exit-only check error: {e}")

    # ── STATUS ───────────────────────────────────────────────────────────
    def status(self):
        next_run = None
        if self._last_run:
            try:
                last = datetime.datetime.fromisoformat(self._last_run.replace("Z", "+00:00"))
                next_run = (last + datetime.timedelta(minutes=self.interval_min)).isoformat()
            except Exception:
                pass
        return {
            "enabled": self.is_enabled(),
            "broker": "alpaca",
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "interval_min": self.interval_min,
            "intraday_intervals": {"15m": INTRADAY_15M_INTERVAL_SEC // 60, "30m": INTRADAY_30M_INTERVAL_SEC // 60},
            "last_run": self._last_run,
            "next_run": next_run,
            "last_result": self._last_result,
            "last_error": self._last_error,
            "recent_runs": self._runs_log[-10:],
        }

    def trigger_now(self):
        """Run one cycle immediately from a separate thread."""
        t = threading.Thread(target=self._run_once, daemon=True)
        t.start()
        return {"triggered": True, "broker": "alpaca"}
