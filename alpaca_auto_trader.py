"""
Strategy Factory — Alpaca Auto-Trader Background Worker

Separate from the simulator auto-trader. Runs on its own toggle, flag file,
and log. Every N minutes (default 30):
  1. Invoke daily_runner.py to refresh portfolio analysis
  2. Execute rebalancing trades on Alpaca paper trading

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
DEFAULT_INTERVAL_MIN = int(
    os.environ.get("ALPACA_AUTO_TRADE_INTERVAL_MIN")
    or os.environ.get("AUTO_TRADE_INTERVAL_MIN")
    or "15"
)


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
        logger.info("AlpacaAutoTrader loop entered")
        while not self._stop.is_set():
            if self.is_enabled():
                try:
                    self._run_once()
                except Exception as e:
                    self._last_error = str(e)
                    logger.error(f"Alpaca auto run failed: {e}", exc_info=True)
                    self._append_log({
                        "timestamp": datetime.datetime.utcnow().isoformat(),
                        "status": "error",
                        "error": str(e),
                    })
            # Sleep in 10-second slices so stop() is responsive
            for _ in range(self.interval_sec // 10):
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
