"""
Strategy Factory — Risk Manager

Comprehensive risk controls for the crypto trading system.
All classes persist state to disk (DATA_DIR) and use UTC timestamps.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import config
from alpaca_client import AlpacaPaperClient

logger = logging.getLogger("risk_manager")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path, default=None):
    """Safely read a JSON file, returning *default* on any failure."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _write_json(path, data):
    """Safely write a JSON file (atomic-ish via tmp + rename)."""
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
    except Exception:
        logger.exception("Failed to write %s", path)


def _utcnow():
    return datetime.now(timezone.utc)


def _today_str():
    return _utcnow().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 1. DrawdownCircuitBreaker
# ---------------------------------------------------------------------------

class DrawdownCircuitBreaker:
    """
    Track peak equity.  If current equity drops below 85 % of peak,
    fire an emergency shutdown: disable auto-trading, close all positions,
    and write an alert file.
    """

    PEAK_FILE = os.path.join(config.DATA_DIR, "peak_equity.json")
    ALERT_FILE = os.path.join(config.DATA_DIR, "circuit_breaker_alert.json")
    FLAG_FILE = os.path.join(config.DATA_DIR, "alpaca_auto_trade.enabled")
    MAX_DRAWDOWN_PCT = 15.0  # trigger at 15 % drawdown from peak

    def __init__(self):
        state = _read_json(self.PEAK_FILE, {})
        self.peak_equity = state.get("peak_equity", 0.0)
        self.peak_updated = state.get("updated_at", "")

    def _persist_peak(self):
        _write_json(self.PEAK_FILE, {
            "peak_equity": self.peak_equity,
            "updated_at": _utcnow().isoformat(),
        })

    def check(self, current_equity: float) -> bool:
        """
        Return True if trading is safe.
        Return False (and fire shutdown) if drawdown threshold is breached.
        """
        if current_equity <= 0:
            return False

        # Update peak
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            self._persist_peak()

        if self.peak_equity <= 0:
            return True

        drawdown_pct = (1.0 - current_equity / self.peak_equity) * 100.0
        if drawdown_pct >= self.MAX_DRAWDOWN_PCT:
            logger.critical(
                "CIRCUIT BREAKER: equity $%.2f is %.1f%% below peak $%.2f",
                current_equity, drawdown_pct, self.peak_equity,
            )
            self._emergency_shutdown(current_equity, drawdown_pct)
            return False

        return True

    def _emergency_shutdown(self, current_equity, drawdown_pct):
        # 1. Remove auto-trade flag
        try:
            if os.path.exists(self.FLAG_FILE):
                os.remove(self.FLAG_FILE)
                logger.warning("Auto-trade flag removed: %s", self.FLAG_FILE)
        except Exception:
            logger.exception("Failed to remove auto-trade flag")

        # 2. Close all positions
        try:
            client = AlpacaPaperClient()
            result = client.close_all_positions()
            logger.warning("Closed all positions: %s", result)
        except Exception:
            logger.exception("Failed to close all positions during circuit breaker")

        # 3. Write alert
        alert = {
            "event": "circuit_breaker",
            "fired_at": _utcnow().isoformat(),
            "peak_equity": self.peak_equity,
            "current_equity": current_equity,
            "drawdown_pct": round(drawdown_pct, 2),
        }
        _write_json(self.ALERT_FILE, alert)
        logger.critical("Circuit breaker alert written to %s", self.ALERT_FILE)

    @property
    def last_alert(self):
        return _read_json(self.ALERT_FILE, None)


# ---------------------------------------------------------------------------
# 2. DailyLossGuard
# ---------------------------------------------------------------------------

class DailyLossGuard:
    """
    Track start-of-day equity.  If current equity drops more than
    *max_daily_loss_pct* from that baseline, block new trades.
    Resets at midnight UTC.
    """

    STATE_FILE = os.path.join(config.DATA_DIR, "daily_loss_guard.json")

    def __init__(self, max_daily_loss_pct: float = 5.0):
        self.max_daily_loss_pct = max_daily_loss_pct
        self._load()

    def _load(self):
        state = _read_json(self.STATE_FILE, {})
        self._date = state.get("date", "")
        self._start_equity = state.get("start_equity", 0.0)

    def _persist(self):
        _write_json(self.STATE_FILE, {
            "date": self._date,
            "start_equity": self._start_equity,
            "updated_at": _utcnow().isoformat(),
        })

    def check(self, current_equity: float) -> bool:
        """
        Return True if daily loss is within limits.
        Automatically resets the baseline at midnight UTC.
        """
        today = _today_str()

        # Reset at midnight or on first call
        if self._date != today or self._start_equity <= 0:
            self._date = today
            self._start_equity = current_equity
            self._persist()
            logger.info("DailyLossGuard reset: start equity $%.2f for %s", current_equity, today)
            return True

        if self._start_equity <= 0:
            return True

        loss_pct = (1.0 - current_equity / self._start_equity) * 100.0
        if loss_pct >= self.max_daily_loss_pct:
            logger.warning(
                "DAILY LOSS GUARD: equity $%.2f is down %.2f%% from SOD $%.2f (limit %.1f%%)",
                current_equity, loss_pct, self._start_equity, self.max_daily_loss_pct,
            )
            return False

        return True

    @property
    def start_equity(self):
        return self._start_equity

    @property
    def date(self):
        return self._date


# ---------------------------------------------------------------------------
# 3. PositionStopLoss
# ---------------------------------------------------------------------------

class PositionStopLoss:
    """
    Scan all open positions and close any that are down more than
    *max_loss_pct* from cost basis.
    """

    STOP_LOG_FILE = os.path.join(config.DATA_DIR, "stop_loss_log.json")

    def __init__(self, max_loss_pct: float = 8.0):
        self.max_loss_pct = max_loss_pct

    def check_and_close(self, client) -> list:
        """
        Return a list of dicts for each position that was closed.
        """
        closed = []
        try:
            positions = client.get_positions()
        except Exception:
            logger.exception("PositionStopLoss: failed to fetch positions")
            return closed

        for pos in positions:
            symbol = pos.get("symbol", "")
            cost_basis = pos.get("cost_basis", 0.0)
            market_value = pos.get("market_value", 0.0)

            if cost_basis <= 0:
                continue

            loss_pct = (1.0 - market_value / cost_basis) * 100.0
            if loss_pct >= self.max_loss_pct:
                logger.warning(
                    "STOP LOSS: %s down %.2f%% (cost $%.2f, value $%.2f) — closing",
                    symbol, loss_pct, cost_basis, market_value,
                )
                try:
                    result = client.close_position(symbol)
                    closed.append({
                        "symbol": symbol,
                        "loss_pct": round(loss_pct, 2),
                        "cost_basis": cost_basis,
                        "market_value": market_value,
                        "closed_at": _utcnow().isoformat(),
                        "result": result,
                    })
                except Exception:
                    logger.exception("Failed to close stop-loss position %s", symbol)

        if closed:
            self._log_stops(closed)

        return closed

    def _log_stops(self, closed):
        """Append stop-loss events to the log file."""
        existing = _read_json(self.STOP_LOG_FILE, [])
        if not isinstance(existing, list):
            existing = []
        existing.extend(closed)
        # Keep last 500 entries
        _write_json(self.STOP_LOG_FILE, existing[-500:])

    def get_recent_stops(self, days: int = 7) -> list:
        """Return stop-loss events from the last *days* days."""
        cutoff = (_utcnow() - timedelta(days=days)).isoformat()
        entries = _read_json(self.STOP_LOG_FILE, [])
        if not isinstance(entries, list):
            return []
        return [e for e in entries if e.get("closed_at", "") >= cutoff]


# ---------------------------------------------------------------------------
# 4. ExposureLimits
# ---------------------------------------------------------------------------

class ExposureLimits:
    """
    Cap single-symbol exposure at 12 % of total equity and total
    exposure at 90 % of equity (10 % cash reserve).
    """

    MAX_SINGLE_PCT = 12.0
    MAX_TOTAL_PCT = 90.0

    def _get_target_usd(self, value):
        if isinstance(value, dict):
            return float(value.get("target_usd", 0.0) or 0.0)
        return float(value or 0.0)

    def _set_target_usd(self, target_by_symbol: dict, symbol: str, value: float):
        if isinstance(target_by_symbol.get(symbol), dict):
            target_by_symbol[symbol]["target_usd"] = round(value, 2)
        else:
            target_by_symbol[symbol] = round(value, 2)

    def apply(self, target_by_symbol: dict, total_equity: float) -> dict:
        """
        Modify *target_by_symbol* (symbol -> notional USD) in place,
        capping per-symbol and total exposure.  Returns the same dict.
        """
        if total_equity <= 0:
            target_by_symbol.clear()
            return target_by_symbol

        max_single = total_equity * self.MAX_SINGLE_PCT / 100.0
        max_total = total_equity * self.MAX_TOTAL_PCT / 100.0

        # Cap individual positions
        for sym in list(target_by_symbol.keys()):
            target_usd = self._get_target_usd(target_by_symbol[sym])
            if target_usd > max_single:
                logger.info(
                    "ExposureLimits: capping %s from $%.2f to $%.2f (%.0f%% of equity)",
                    sym, target_usd, max_single, self.MAX_SINGLE_PCT,
                )
                self._set_target_usd(target_by_symbol, sym, max_single)

        # Cap total exposure
        total = sum(self._get_target_usd(v) for v in target_by_symbol.values())
        if total > max_total and total > 0:
            scale = max_total / total
            logger.info(
                "ExposureLimits: scaling total from $%.2f to $%.2f (%.0f%% cap)",
                total, max_total, self.MAX_TOTAL_PCT,
            )
            for sym in list(target_by_symbol.keys()):
                self._set_target_usd(
                    target_by_symbol,
                    sym,
                    self._get_target_usd(target_by_symbol[sym]) * scale,
                )

        return target_by_symbol


# ---------------------------------------------------------------------------
# 5. TradeFrequencyLimiter
# ---------------------------------------------------------------------------

class TradeFrequencyLimiter:
    """
    Limit to 50 total orders per day and 5 orders per symbol per day.
    Resets at midnight UTC.
    """

    MAX_DAILY_TOTAL = 50
    MAX_DAILY_PER_SYMBOL = 5
    STATE_FILE = os.path.join(config.DATA_DIR, "trade_frequency.json")

    def __init__(self):
        self._load()

    def _load(self):
        state = _read_json(self.STATE_FILE, {})
        self._date = state.get("date", "")
        self._total = state.get("total", 0)
        self._by_symbol = state.get("by_symbol", {})
        self._maybe_reset()

    def _maybe_reset(self):
        today = _today_str()
        if self._date != today:
            self._date = today
            self._total = 0
            self._by_symbol = {}
            self._persist()

    def _persist(self):
        _write_json(self.STATE_FILE, {
            "date": self._date,
            "total": self._total,
            "by_symbol": self._by_symbol,
            "updated_at": _utcnow().isoformat(),
        })

    def can_trade(self, symbol: str) -> bool:
        """Return True if the order would not violate frequency limits."""
        self._maybe_reset()
        if self._total >= self.MAX_DAILY_TOTAL:
            logger.warning("TradeFrequencyLimiter: daily total limit reached (%d)", self._total)
            return False
        sym_count = self._by_symbol.get(symbol, 0)
        if sym_count >= self.MAX_DAILY_PER_SYMBOL:
            logger.warning(
                "TradeFrequencyLimiter: symbol %s limit reached (%d)", symbol, sym_count,
            )
            return False
        return True

    def record_trade(self, symbol: str):
        """Increment counters for a placed order."""
        self._maybe_reset()
        self._total += 1
        self._by_symbol[symbol] = self._by_symbol.get(symbol, 0) + 1
        self._persist()

    @property
    def daily_total(self):
        self._maybe_reset()
        return self._total


# ---------------------------------------------------------------------------
# 5b. DuplicateOrderGuard
# ---------------------------------------------------------------------------

class DuplicateOrderGuard:
    """
    Block repeated same-symbol/same-side orders inside a short time window.
    This catches accidental double-clicks, retries, and runaway loops without
    preventing the bot from taking fresh intraday setups later.
    """

    STATE_FILE = os.path.join(config.DATA_DIR, "duplicate_order_guard.json")
    MIN_REPEAT_SECONDS = int(os.environ.get("DUPLICATE_ORDER_MIN_REPEAT_SECONDS", str(12 * 60)))

    def __init__(self):
        self._state = _read_json(self.STATE_FILE, {})
        if not isinstance(self._state, dict):
            self._state = {}

    def can_submit(self, symbol: str, side: str) -> bool:
        key = f"{symbol}:{side.lower()}"
        last_ts = self._state.get(key)
        if not last_ts:
            return True
        try:
            last = datetime.fromisoformat(last_ts)
            if _utcnow() - last < timedelta(seconds=self.MIN_REPEAT_SECONDS):
                logger.warning("DuplicateOrderGuard: blocked duplicate %s", key)
                return False
        except Exception:
            return True
        return True

    def record(self, symbol: str, side: str):
        key = f"{symbol}:{side.lower()}"
        self._state[key] = _utcnow().isoformat()
        _write_json(self.STATE_FILE, self._state)


# ---------------------------------------------------------------------------
# 6. CooldownManager
# ---------------------------------------------------------------------------

class CooldownManager:
    """
    Return an exposure multiplier (0.0 – 1.0) based on recent risk events.
    - 0.25 if circuit breaker fired in last 7 days
    - 0.50 if daily loss limit was hit yesterday
    - 1.00 otherwise
    """

    def __init__(self):
        pass

    def get_multiplier(self) -> float:
        # Check circuit breaker (last 7 days)
        cb_alert = _read_json(DrawdownCircuitBreaker.ALERT_FILE, None)
        if cb_alert and isinstance(cb_alert, dict):
            fired_at = cb_alert.get("fired_at", "")
            try:
                fired_dt = datetime.fromisoformat(fired_at)
                if _utcnow() - fired_dt < timedelta(days=7):
                    logger.info("CooldownManager: circuit breaker fired within 7 days — multiplier 0.25")
                    return 0.25
            except Exception:
                pass

        # Check daily loss guard (yesterday)
        dl_state = _read_json(DailyLossGuard.STATE_FILE, {})
        yesterday = (_utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        if dl_state.get("date") == yesterday:
            start_eq = dl_state.get("start_equity", 0.0)
            # If the guard state is from yesterday and equity dropped past the
            # default threshold, that means the guard tripped.  We check for
            # a separate marker written below, but as a heuristic the file
            # existing for yesterday implies it was active.
            pass

        # More reliable: check the daily-loss-hit marker
        hit_file = os.path.join(config.DATA_DIR, "daily_loss_hit.json")
        hit = _read_json(hit_file, None)
        if hit and isinstance(hit, dict):
            hit_date = hit.get("date", "")
            if hit_date == yesterday:
                logger.info("CooldownManager: daily loss limit hit yesterday — multiplier 0.50")
                return 0.50

        return 1.0

    @staticmethod
    def record_daily_loss_hit():
        """Called by RiskManager when daily loss guard triggers."""
        hit_file = os.path.join(config.DATA_DIR, "daily_loss_hit.json")
        _write_json(hit_file, {
            "date": _today_str(),
            "recorded_at": _utcnow().isoformat(),
        })


# ---------------------------------------------------------------------------
# 7. StrategyDisabler
# ---------------------------------------------------------------------------

class StrategyDisabler:
    """
    Persist disabled strategies to disk.  Disable a bot for 30 days when:
    - 7+ consecutive losing days, OR
    - rolling Sharpe < -0.5, OR
    - 3+ stop-losses in a week.
    """

    STATE_FILE = os.path.join(config.DATA_DIR, "disabled_strategies.json")
    DISABLE_DAYS = 30

    CONSEC_LOSS_THRESHOLD = 7
    SHARPE_THRESHOLD = -0.5
    STOP_LOSS_WEEK_THRESHOLD = 3

    def __init__(self):
        self._disabled = _read_json(self.STATE_FILE, {})

    def _persist(self):
        _write_json(self.STATE_FILE, self._disabled)

    def should_trade(self, bot_name: str) -> bool:
        """Return False if the bot is disabled and the expiry has not passed."""
        entry = self._disabled.get(bot_name)
        if entry is None:
            return True
        expires = entry.get("expires_at", "")
        try:
            exp_dt = datetime.fromisoformat(expires)
            if _utcnow() >= exp_dt:
                # Expiry passed — re-enable
                logger.info("StrategyDisabler: %s expiry passed, re-enabling", bot_name)
                del self._disabled[bot_name]
                self._persist()
                return True
            return False
        except Exception:
            return True

    def check_and_disable(
        self,
        bot_name: str,
        consecutive_loss_days: int = 0,
        rolling_sharpe: float = 0.0,
        stop_losses_week: int = 0,
    ) -> bool:
        """
        Evaluate whether *bot_name* should be disabled.
        Returns True if the bot was just disabled (or already disabled).
        """
        reasons = []

        if consecutive_loss_days >= self.CONSEC_LOSS_THRESHOLD:
            reasons.append(f"{consecutive_loss_days} consecutive losing days")

        if rolling_sharpe < self.SHARPE_THRESHOLD:
            reasons.append(f"rolling Sharpe {rolling_sharpe:.2f}")

        if stop_losses_week >= self.STOP_LOSS_WEEK_THRESHOLD:
            reasons.append(f"{stop_losses_week} stop-losses this week")

        if not reasons:
            return False

        now = _utcnow()
        expires = now + timedelta(days=self.DISABLE_DAYS)
        self._disabled[bot_name] = {
            "disabled_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "reasons": reasons,
        }
        self._persist()
        logger.warning(
            "StrategyDisabler: disabled %s until %s — %s",
            bot_name, expires.isoformat(), "; ".join(reasons),
        )
        return True

    def get_all_disabled(self) -> dict:
        """Return the full disabled-strategies dict."""
        # Refresh from disk in case another process wrote
        self._disabled = _read_json(self.STATE_FILE, {})
        return dict(self._disabled)


# ---------------------------------------------------------------------------
# 8. RiskManager (Facade)
# ---------------------------------------------------------------------------

class RiskManager:
    """
    Facade that initialises and orchestrates all risk controls.
    """

    def __init__(self, max_daily_loss_pct: float = 5.0, max_position_loss_pct: float = 12.0):
        self.circuit_breaker = DrawdownCircuitBreaker()
        self.daily_loss_guard = DailyLossGuard(max_daily_loss_pct=max_daily_loss_pct)
        self.position_stop_loss = PositionStopLoss(max_loss_pct=max_position_loss_pct)
        self.exposure_limits = ExposureLimits()
        self.frequency_limiter = TradeFrequencyLimiter()
        self.duplicate_guard = DuplicateOrderGuard()
        self.cooldown = CooldownManager()
        self.strategy_disabler = StrategyDisabler()
        logger.info("RiskManager initialised")

    # ── Pre-trade gate ─────────────────────────────────────────────────
    def pre_trade_check(self, current_equity: float) -> tuple:
        """
        Run circuit breaker, daily loss guard, and cooldown checks.
        Returns (ok: bool, reasons: list[str]).
        """
        ok = True
        reasons = []

        if not self.circuit_breaker.check(current_equity):
            ok = False
            reasons.append("circuit breaker tripped (equity below 85% of peak)")

        if not self.daily_loss_guard.check(current_equity):
            ok = False
            reasons.append(
                f"daily loss limit exceeded ({self.daily_loss_guard.max_daily_loss_pct}%)"
            )
            CooldownManager.record_daily_loss_hit()

        multiplier = self.cooldown.get_multiplier()
        if multiplier < 1.0:
            reasons.append(f"cooldown active (exposure multiplier {multiplier})")

        if reasons:
            logger.warning("pre_trade_check: %s", "; ".join(reasons))

        return ok, reasons

    # ── Position stop losses ───────────────────────────────────────────
    def enforce_position_stops(self, client) -> list:
        """Close any positions breaching the stop-loss threshold."""
        return self.position_stop_loss.check_and_close(client)

    # ── Exposure limits ────────────────────────────────────────────────
    def apply_exposure_limits(self, target_by_symbol: dict, equity: float) -> dict:
        """Cap per-symbol and total exposure."""
        return self.exposure_limits.apply(target_by_symbol, equity)

    # ── Frequency limits ───────────────────────────────────────────────
    def can_place_order(self, symbol: str) -> bool:
        """Check whether an order for *symbol* is within frequency limits."""
        return self.frequency_limiter.can_trade(symbol)

    def record_order(self, symbol: str):
        """Record that an order was placed for *symbol*."""
        self.frequency_limiter.record_trade(symbol)

    def can_submit_order(self, symbol: str, side: str) -> bool:
        """Check duplicate order guard for a symbol/side pair."""
        return self.duplicate_guard.can_submit(symbol, side)

    def record_submitted_order(self, symbol: str, side: str):
        """Record duplicate guard state for the submitted symbol/side pair."""
        self.duplicate_guard.record(symbol, side)

    def should_trade_strategy(self, bot_name: str) -> bool:
        """Return True when the persisted strategy disabler allows trading."""
        return self.strategy_disabler.should_trade(bot_name)

    def update_strategy_disable_state(
        self,
        bot_name: str,
        consecutive_loss_days: int = 0,
        rolling_sharpe: float = 0.0,
        stop_losses_week: int = 0,
    ) -> bool:
        """Persist a strategy disable when real-paper damage is sustained."""
        return self.strategy_disabler.check_and_disable(
            bot_name,
            consecutive_loss_days=consecutive_loss_days,
            rolling_sharpe=rolling_sharpe,
            stop_losses_week=stop_losses_week,
        )

    # ── Cooldown multiplier ────────────────────────────────────────────
    def get_exposure_multiplier(self) -> float:
        """Return the current cooldown exposure multiplier (0.0 – 1.0)."""
        return self.cooldown.get_multiplier()

    # ── Dashboard status ───────────────────────────────────────────────
    def get_status(self) -> dict:
        """Return a dict summarising all risk-control states for the dashboard."""
        now = _utcnow()
        cb_alert = self.circuit_breaker.last_alert
        cb_active = False
        if cb_alert and isinstance(cb_alert, dict):
            try:
                fired_dt = datetime.fromisoformat(cb_alert["fired_at"])
                cb_active = (now - fired_dt) < timedelta(days=7)
            except Exception:
                pass

        return {
            "timestamp": now.isoformat(),
            "circuit_breaker": {
                "peak_equity": self.circuit_breaker.peak_equity,
                "active": cb_active,
                "last_alert": cb_alert,
            },
            "daily_loss_guard": {
                "date": self.daily_loss_guard.date,
                "start_equity": self.daily_loss_guard.start_equity,
                "max_daily_loss_pct": self.daily_loss_guard.max_daily_loss_pct,
            },
            "exposure_limits": {
                "max_single_pct": ExposureLimits.MAX_SINGLE_PCT,
                "max_total_pct": ExposureLimits.MAX_TOTAL_PCT,
            },
            "frequency_limiter": {
                "daily_total": self.frequency_limiter.daily_total,
                "max_daily_total": TradeFrequencyLimiter.MAX_DAILY_TOTAL,
                "max_per_symbol": TradeFrequencyLimiter.MAX_DAILY_PER_SYMBOL,
            },
            "duplicate_order_guard": {
                "min_repeat_seconds": DuplicateOrderGuard.MIN_REPEAT_SECONDS,
            },
            "cooldown": {
                "exposure_multiplier": self.cooldown.get_multiplier(),
            },
            "disabled_strategies": self.strategy_disabler.get_all_disabled(),
        }
