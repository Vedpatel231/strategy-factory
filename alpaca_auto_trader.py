"""
Strategy Factory — Alpaca Auto-Trader Background Worker

Multi-schedule worker:
  - 15m professional trading desk cycle: CEO → asset managers → risk → Alpaca
  - 15m exit checks: ATR trailing, hard stop, ADX exit, TP for all positions

Each main cycle:
  1. Run CEO market intelligence
  2. Let each asset manager rank its configured entry-timeframe strategy bots
  3. Send valid trade requests through risk and Alpaca paper execution

Legacy 15m/30m stock cycles are disabled unless ENABLE_LEGACY_INTRADAY=true.

Controlled via data/alpaca_auto_trade.enabled flag file.
"""

import os
import json
import time
import logging
import threading
import datetime

import config

logger = logging.getLogger("alpaca_auto_trader")

DATA_DIR = config.DATA_DIR
REPORT_DIR = config.REPORT_DIR
FLAG_FILE = os.path.join(DATA_DIR, "alpaca_auto_trade.enabled")
LOG_FILE = os.path.join(DATA_DIR, "alpaca_auto_trade.log.json")
# Main professional desk cycle runs every 15 minutes. Entries use the
# configured desk timeframe, with 4H/1D confirmation used by managers.
DEFAULT_INTERVAL_MIN = int(
    os.environ.get("ALPACA_AUTO_TRADE_INTERVAL_MIN")
    or os.environ.get("AUTO_TRADE_INTERVAL_MIN")
    or str(getattr(config, "DESK_CYCLE_INTERVAL_MIN", 15))
)
EXIT_CHECK_INTERVAL_MIN = int(os.environ.get("EXIT_CHECK_INTERVAL_MIN", "15"))
ENABLE_LEGACY_INTRADAY = os.environ.get("ENABLE_LEGACY_INTRADAY", "false").lower() == "true"

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

    def _write_last_refresh(self, desk_state=None):
        """Update last_refresh.json so the dashboard badge stays current."""
        try:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            try:
                from zoneinfo import ZoneInfo
                human_est = now_utc.astimezone(ZoneInfo("America/New_York")).strftime("%b %d, %Y %I:%M %p %Z")
            except Exception:
                human_est = now_utc.strftime("%Y-%m-%d %H:%M UTC")
            desk_state = desk_state or {}
            summary = (desk_state or {}).get("summary", {})
            registry = desk_state.get("registry") or {}
            portfolio_summary = {}
            try:
                portfolio_path = os.path.join(REPORT_DIR, "latest_portfolio.json")
                if os.path.exists(portfolio_path):
                    with open(portfolio_path) as f:
                        portfolio_summary = (json.load(f).get("summary") or {})
            except Exception:
                portfolio_summary = {}
            payload = {
                "refreshed": True,
                "triggered_by": "alpaca_auto",
                "timestamp_utc": now_utc.isoformat(),
                "timestamp_iso": now_utc.isoformat(),
                "display_est": human_est,
                "num_strategies": summary.get("bots") or registry.get("bots") or portfolio_summary.get("num_strategies", 0),
                "num_managers": summary.get("managers", 0),
                "entry_candidates": summary.get("manager_enter_decisions", summary.get("enter_decisions", 0)),
                "risk_approved_entries": summary.get("risk_approved_entries", 0),
                "blocked_after_manager": summary.get("blocked_after_manager", 0),
                "submitted": summary.get("orders_submitted", 0),
                "rejected": summary.get("orders_rejected", 0),
                "expected_monthly_return_pct": portfolio_summary.get("expected_monthly_return_pct", 0),
                "regime": (desk_state.get("ceo") or {}).get("market_regime"),
                "posture": (desk_state.get("ceo") or {}).get("posture"),
            }
            path = os.path.join(DATA_DIR, "last_refresh.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(payload, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Could not write last_refresh.json: {e}")

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
        logger.info("AlpacaAutoTrader loop entered (15m professional desk + 15m exit checks)")
        exit_check_sec = EXIT_CHECK_INTERVAL_MIN * 60
        last_main_run = 0
        last_15m_run = 0
        last_30m_run = 0

        while not self._stop.is_set():
            if self.is_enabled():
                now = time.time()
                # Main cycle: run every N minutes (entry checks + full analysis)
                if now - last_main_run >= self.interval_sec:
                    try:
                        self._run_once()
                        last_main_run = time.time()  # use fresh timestamp after run
                    except Exception as e:
                        self._last_error = str(e)
                        logger.error(f"Alpaca auto run failed: {e}", exc_info=True)
                        self._append_log({
                            "timestamp": datetime.datetime.utcnow().isoformat(),
                            "status": "error",
                            "error": str(e),
                        })
                        last_main_run = time.time()  # don't retry immediately

                # Refresh now after main cycle (which can take minutes)
                now = time.time()

                # Intraday cycles: stocks only, market hours only
                try:
                    from alpaca_client import is_us_market_open
                    market_open = is_us_market_open()
                except Exception as e:
                    logger.warning(f"Market open check failed: {e}")
                    market_open = False

                if ENABLE_LEGACY_INTRADAY and market_open:
                    # 15m intraday cycle
                    if now - last_15m_run >= INTRADAY_15M_INTERVAL_SEC:
                        try:
                            self._run_intraday_cycle("15m")
                            last_15m_run = time.time()
                        except Exception as e:
                            logger.warning(f"Intraday 15m cycle failed: {e}", exc_info=True)
                            self._append_log({
                                "timestamp": datetime.datetime.utcnow().isoformat(),
                                "status": "error",
                                "cycle_type": "intraday_15m",
                                "error": str(e),
                            })
                            last_15m_run = time.time()

                    # 30m intraday cycle
                    if now - last_30m_run >= INTRADAY_30M_INTERVAL_SEC:
                        try:
                            self._run_intraday_cycle("30m")
                            last_30m_run = time.time()
                        except Exception as e:
                            logger.warning(f"Intraday 30m cycle failed: {e}", exc_info=True)
                            self._append_log({
                                "timestamp": datetime.datetime.utcnow().isoformat(),
                                "status": "error",
                                "cycle_type": "intraday_30m",
                                "error": str(e),
                            })
                            last_30m_run = time.time()
                elif ENABLE_LEGACY_INTRADAY:
                    logger.debug(f"US market closed — skipping intraday cycles")

                # Between main/intraday cycles: check exits
                try:
                    exit_info = self._check_exits_only()
                    if exit_info:
                        self._append_log({
                            "timestamp": datetime.datetime.utcnow().isoformat(),
                            "status": "ok",
                            "cycle_type": "exit_check",
                            "exits": exit_info,
                        })
                except Exception as e:
                    logger.warning(f"Exit check failed: {e}")

                # EOD profit-taking + Friday close-all
                try:
                    from eod_manager import check_eod
                    eod_result = check_eod()
                    if eod_result:
                        self._append_log({
                            "timestamp": datetime.datetime.utcnow().isoformat(),
                            "status": "ok",
                            "cycle_type": "eod_close",
                            "result": eod_result,
                        })
                        self._refresh_live_monitor()
                except Exception as e:
                    logger.warning(f"EOD check failed: {e}")

            # Sleep in 10-second slices for exit_check_interval
            for _ in range(exit_check_sec // 10):
                if self._stop.is_set():
                    return
                time.sleep(10)

    def _run_once(self):
        """One full cycle: re-analyze, then rebalance on Alpaca."""
        start_ts = utc_now()
        logger.info("🦙 Professional trading desk cycle start")

        entry = {
            "timestamp": start_ts.isoformat(),
            "status": "running",
            "broker": "alpaca",
            "cycle_type": "trading_desk",
            "steps": {},
        }

        try:
            from trading_desk import TradingDeskEngine
            desk_state = TradingDeskEngine().run_cycle(dry_run=False)
            entry["steps"]["trading_desk"] = {
                "ok": True,
                "summary": desk_state.get("summary", {}),
                "ceo": desk_state.get("ceo", {}),
                "broker_note": desk_state.get("broker_note", ""),
            }
            entry["status"] = "ok"
            entry["duration_sec"] = (utc_now() - start_ts).total_seconds()
            self._last_run = start_ts.isoformat()
            self._last_result = entry
            self._last_error = None
            self._append_log(entry)
            self._refresh_live_monitor()
            self._write_last_refresh(desk_state)
            logger.info(f"🦙 Professional trading desk cycle complete ({entry['status']})")
            return
        except Exception as e:
            logger.error(f"Professional trading desk cycle failed: {e}", exc_info=True)
            entry["steps"]["trading_desk"] = {"ok": False, "error": str(e)}
            entry["status"] = "error"
            entry["error"] = str(e)
            entry["duration_sec"] = (utc_now() - start_ts).total_seconds()
            self._last_run = start_ts.isoformat()
            self._last_result = entry
            self._last_error = str(e)
            self._append_log(entry)
            self._refresh_live_monitor()
            return

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
        eval_details = []  # track per-symbol evaluation for logging

        logger.info(f"  Evaluating {len(stock_symbols)} stocks, {len(intraday_strategies)} strategies, "
                     f"held={list(held_symbols)}, stock_positions={stock_count}/{config.MAX_CONCURRENT_INTRADAY_STOCKS}")

        for symbol in stock_symbols:
            # Skip if already holding this symbol
            if symbol in held_symbols:
                eval_details.append({"symbol": symbol, "skip": "already_held"})
                continue
            # Concurrency limit for intraday stocks
            if stock_count >= config.MAX_CONCURRENT_INTRADAY_STOCKS:
                eval_details.append({"symbol": symbol, "skip": "max_positions"})
                logger.info(f"  Max {config.MAX_CONCURRENT_INTRADAY_STOCKS} intraday stock positions — skipping rest")
                break

            best_for_symbol = None
            for strategy_name in intraday_strategies:
                try:
                    result = engine.evaluate_symbol_intraday(symbol, strategy_name, timeframe)
                    action = result.get("action", "hold")
                    confidence = float(result.get("confidence", 0))
                    accepted = result.get("accepted", False)
                    reason = result.get("reason", "")

                    logger.info(f"  {symbol}/{strategy_name}/{timeframe}: action={action} conf={confidence:.2f} accepted={accepted} reason={reason[:80]}")

                    if action != "buy" or not accepted or confidence < 0.60:
                        if not best_for_symbol or confidence > best_for_symbol.get("confidence", 0):
                            best_for_symbol = {"strategy": strategy_name, "action": action,
                                               "confidence": round(confidence, 3), "reason": reason[:100]}
                        continue

                    signals_found += 1

                    # Find matching allocation for sizing
                    alloc_usd = 0
                    for a in allocations:
                        if a.get("strategy_type") == strategy_name and symbol in a.get("pair", ""):
                            alloc_usd = a.get("allocation_usd", 0)
                            break

                    if alloc_usd < 10:
                        logger.info(f"  {strategy_name} {timeframe} {symbol}: signal accepted but no allocation (${alloc_usd:.2f})")
                        eval_details.append({"symbol": symbol, "strategy": strategy_name,
                                             "skip": "no_allocation", "alloc_usd": alloc_usd})
                        continue

                    # Risk checks
                    try:
                        if not rm.can_place_order(symbol):
                            eval_details.append({"symbol": symbol, "strategy": strategy_name, "skip": "risk_order_limit"})
                            continue
                        if not rm.can_submit_order(symbol, "buy"):
                            eval_details.append({"symbol": symbol, "strategy": strategy_name, "skip": "risk_submit_limit"})
                            continue
                    except Exception:
                        pass

                    order_usd = min(alloc_usd, buying_power * 0.9)  # leave 10% buffer
                    if order_usd < 5:
                        eval_details.append({"symbol": symbol, "strategy": strategy_name,
                                             "skip": "insufficient_buying_power", "order_usd": round(order_usd, 2)})
                        continue

                    order_result = client.submit_order(symbol, order_usd, side="buy")
                    trades_executed += 1
                    stock_count += 1
                    buying_power -= order_usd
                    held_symbols.add(symbol)
                    eval_details.append({"symbol": symbol, "strategy": strategy_name,
                                         "action": "BUY", "usd": round(order_usd, 2), "confidence": round(confidence, 3)})

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
                    logger.warning(f"  Eval/trade error {strategy_name}/{timeframe}/{symbol}: {e}")
                    eval_details.append({"symbol": symbol, "strategy": strategy_name, "error": str(e)[:100]})

            # Log best signal if no trade was placed for this symbol
            if best_for_symbol and not any(d.get("symbol") == symbol and d.get("action") == "BUY" for d in eval_details):
                eval_details.append({"symbol": symbol, "best_signal": best_for_symbol})

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
            "equity": equity,
            "buying_power": buying_power,
            "details": eval_details[:20],  # cap to avoid huge logs
        })

        if trades_executed > 0:
            self._refresh_live_monitor()

    def _check_exits_only(self):
        """Quick exit check between main cycles — stop, TP, partial, trailing.
        Returns dict with exit info if any positions were checked/closed."""
        try:
            from alpaca_client import AlpacaPaperClient
            from exit_manager import ExitManager

            client = AlpacaPaperClient()
            positions_list = client.get_positions()
            if not positions_list:
                return None  # no open positions, nothing to check

            exit_result = ExitManager(client=client).check_exits(positions=positions_list, dry_run=False)
            exit_orders = exit_result.get("actions", [])
            info = {
                "positions_checked": len(positions_list),
                "symbols": [p.get("symbol") for p in positions_list],
                "exits_triggered": len(exit_orders) if exit_orders else 0,
            }
            if exit_orders:
                logger.info(f"Exit check closed {len(exit_orders)} positions")
                info["exit_details"] = [
                    {"symbol": o.get("symbol"), "reason": o.get("reason", "")}
                    for o in exit_orders[:5]
                ]
                self._refresh_live_monitor()
            return info
        except Exception as e:
            logger.debug(f"Exit-only check error: {e}")
            return None

    # ── STATUS ───────────────────────────────────────────────────────────
    def status(self):
        next_run = None
        if self._last_run:
            try:
                last = datetime.datetime.fromisoformat(self._last_run.replace("Z", "+00:00"))
                next_run = (last + datetime.timedelta(minutes=self.interval_min)).isoformat()
            except Exception:
                pass

        # Check market status
        try:
            from alpaca_client import is_us_market_open
            market_open = is_us_market_open()
        except Exception:
            market_open = None

        # Count recent cycle types for dashboard clarity.
        desk_runs = sum(1 for r in self._runs_log if r.get("cycle_type") == "trading_desk")
        intraday_runs = sum(1 for r in self._runs_log if "intraday" in r.get("cycle_type", ""))
        exit_check_runs = sum(1 for r in self._runs_log if r.get("cycle_type") == "exit_check")

        # EOD manager status
        eod_status = {}
        try:
            from eod_manager import get_status as eod_get_status
            eod_status = eod_get_status()
        except Exception:
            pass

        return {
            "enabled": self.is_enabled(),
            "broker": "alpaca",
            "engine": "professional_trading_desk",
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "interval_min": self.interval_min,
            "entry_timeframe": getattr(config, "DESK_ENTRY_TIMEFRAME", "1h"),
            "confirm_timeframes": getattr(config, "DESK_CONFIRM_TIMEFRAMES", ["4h", "1D"]),
            "intraday_intervals": {"legacy_enabled": ENABLE_LEGACY_INTRADAY},
            "us_market_open": market_open,
            "last_run": self._last_run,
            "next_run": next_run,
            "last_result": self._last_result,
            "last_error": self._last_error,
            "trading_desk_runs_total": desk_runs,
            "intraday_runs_total": intraday_runs,
            "exit_check_runs_total": exit_check_runs,
            "recent_runs": self._runs_log[-15:],
            "eod_manager": eod_status,
        }

    def trigger_now(self):
        """Run one cycle immediately from a separate thread."""
        t = threading.Thread(target=self._run_once, daemon=True)
        t.start()
        return {"triggered": True, "broker": "alpaca"}
