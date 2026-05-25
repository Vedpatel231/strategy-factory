"""
Strategy Factory — Auto-Trader Background Worker

Runs continuously in the background when the Flask server starts.
Every 30 minutes (configurable):
  1. Invoke daily_runner.py to re-evaluate all bots and regenerate portfolio
  2. Auto-execute rebalancing trades via PaperBroker (if enabled)

Controlled via a simple on/off flag persisted to data/auto_trade.enabled
so the state survives server restarts.

Exposes:
    AutoTrader.start()  — launch background thread
    AutoTrader.status() — snapshot of current state
    AutoTrader.set_enabled(bool)
"""

import os
import json
import time
import logging
import threading
import subprocess
import datetime

import config

logger = logging.getLogger("auto_trader")

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = config.DATA_DIR
REPORT_DIR = config.REPORT_DIR
FLAG_FILE = os.path.join(DATA_DIR, "auto_trade.enabled")
LOG_FILE = os.path.join(DATA_DIR, "auto_trade.log.json")
DEFAULT_INTERVAL_MIN = int(os.environ.get("AUTO_TRADE_INTERVAL_MIN", "30"))


def utc_now():
    """Return a timezone-aware UTC timestamp."""
    return datetime.datetime.now(datetime.timezone.utc)


class AutoTrader:
    """Background thread that refreshes analysis + rebalances every N minutes."""

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
            cls._instance = AutoTrader()
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
        self._runs_log = self._runs_log[-100:]  # keep last 100
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(LOG_FILE, "w") as f:
                json.dump(self._runs_log, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Could not write log: {e}")

    # ── WORKER LOOP ──────────────────────────────────────────────────────
    def start(self):
        if self._thread and self._thread.is_alive():
            logger.info("AutoTrader already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="AutoTrader")
        self._thread.start()
        logger.info(f"AutoTrader thread started (interval {self.interval_min}min)")

    def stop(self):
        self._stop.set()

    def _loop(self):
        logger.info("AutoTrader loop entered")
        while not self._stop.is_set():
            if self.is_enabled():
                try:
                    self._run_once()
                except Exception as e:
                    self._last_error = str(e)
                    logger.error(f"Auto run failed: {e}", exc_info=True)
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
        """One full cycle: re-analyze, then rebalance."""
        start_ts = utc_now()
        logger.info("🤖 Auto-trade cycle start")

        entry = {
            "timestamp": start_ts.isoformat(),
            "status": "running",
            "steps": {},
        }

        # Step 1: Run daily_runner (refresh analysis + dashboard)
        env = dict(os.environ)
        env["SF_TRIGGER"] = "auto"
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
                self._last_result = entry
                return
        except subprocess.TimeoutExpired:
            entry["status"] = "timeout"
            self._append_log(entry)
            self._last_result = entry
            return

        # Step 2: Load fresh portfolio, execute rebalance
        try:
            portfolio_path = os.path.join(REPORT_DIR, "latest_portfolio.json")
            with open(portfolio_path) as f:
                portfolio = json.load(f)
            # Import lazily so sandbox without flask/etc still loads
            from paper_trader import PaperTrader
            trader = PaperTrader(starting_balance=1000.0)
            results = trader.execute_portfolio(portfolio, dry_run=False)
            entry["steps"]["trade"] = {
                "ok": True,
                "summary": results.get("summary", {}),
                "equity_after": trader.client.get_account()["equity"],
            }
            # Record daily P&L snapshot for calendar
            try:
                trader.client.record_daily_snapshot()
            except Exception as snap_err:
                logger.warning(f"Daily snapshot failed: {snap_err}")
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
        logger.info(f"🤖 Auto-trade cycle complete ({entry['status']})")

    # ── STATUS ───────────────────────────────────────────────────────────
    def status(self):
        """Return a snapshot for the /api/auto/status endpoint."""
        next_run = None
        if self._last_run:
            try:
                last = datetime.datetime.fromisoformat(self._last_run.replace("Z", "+00:00"))
                next_run = (last + datetime.timedelta(minutes=self.interval_min)).isoformat()
            except Exception:
                pass
        return {
            "enabled": self.is_enabled(),
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
        return {"triggered": True}
