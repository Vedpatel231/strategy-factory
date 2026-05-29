"""
Strategy Factory — Risk Manager

Comprehensive risk controls for the ETF trading system.
All classes persist state to disk (DATA_DIR) and use US Eastern timestamps
so that daily resets align with conservative_mode and eod_manager.
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import config
from alpaca_client import AlpacaPaperClient

logger = logging.getLogger("risk_manager")


def _send_risk_alert(message, level="critical"):
    """Fire-and-forget Telegram alert for risk events."""
    try:
        from telegram_notifier import send_alert, is_configured
        if is_configured():
            send_alert(message, level=level)
    except Exception as e:
        logger.debug(f"Telegram risk alert failed: {e}")

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


def _et_now():
    """Current time in US Eastern — matches conservative_mode and eod_manager."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def _today_str():
    return _et_now().strftime("%Y-%m-%d")


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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
            "updated_at": _et_now().isoformat(),
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
        # 0. IMMEDIATE Telegram alert — trader must know NOW
        _send_risk_alert(
            f"CIRCUIT BREAKER FIRED\n\n"
            f"Equity: ${current_equity:,.2f}\n"
            f"Peak: ${self.peak_equity:,.2f}\n"
            f"Drawdown: {drawdown_pct:.1f}%\n\n"
            f"Auto-trading DISABLED. All positions being closed.",
            level="critical",
        )

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
            "fired_at": _et_now().isoformat(),
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
    Resets at midnight ET (aligned with conservative_mode).
    """

    STATE_FILE = os.path.join(config.DATA_DIR, "daily_loss_guard.json")

    # NOTE: 2.0% is the single source of truth for the hard daily-loss
    # block.  RiskManager constructs this guard with max_daily_loss_pct=2.0,
    # so this default is kept at 2.0 to match the live behavior (an older
    # 5.0 default here was misleading — nothing in the live path used it).
    def __init__(self, max_daily_loss_pct: float = 2.0):
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
            "updated_at": _et_now().isoformat(),
        })

    def check(self, current_equity: float) -> bool:
        """
        Return True if daily loss is within limits.
        Automatically resets the baseline at midnight ET.
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
            _send_risk_alert(
                f"DAILY LOSS LIMIT HIT\n\n"
                f"Equity: ${current_equity:,.2f}\n"
                f"Start of day: ${self._start_equity:,.2f}\n"
                f"Day loss: {loss_pct:.1f}% (limit: {self.max_daily_loss_pct}%)\n\n"
                f"New trades BLOCKED until tomorrow.",
                level="critical",
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
            cost_basis = _safe_float(pos.get("cost_basis"))
            market_value = _safe_float(pos.get("market_value"))

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
                        "closed_at": _et_now().isoformat(),
                        "result": result,
                    })
                except Exception:
                    logger.exception("Failed to close stop-loss position %s", symbol)

        if closed:
            self._log_stops(closed)
            # Alert on every stop loss closure
            for c in closed:
                _send_risk_alert(
                    f"POSITION STOP LOSS\n\n"
                    f"Symbol: {c.get('symbol')}\n"
                    f"Loss: {c.get('loss_pct', 0):.1f}%\n"
                    f"Cost: ${c.get('cost_basis', 0):,.2f}\n"
                    f"Value: ${c.get('market_value', 0):,.2f}",
                    level="warning",
                )

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
        cutoff = (_et_now() - timedelta(days=days)).isoformat()
        entries = _read_json(self.STOP_LOG_FILE, [])
        if not isinstance(entries, list):
            return []
        return [e for e in entries if e.get("closed_at", "") >= cutoff]


# ---------------------------------------------------------------------------
# 4. ExposureLimits
# ---------------------------------------------------------------------------

class ExposureLimits:
    """
    Cap single-symbol exposure at 12 % of total equity, the combined
    broad-equity index sleeve (QQQ + SPY) at 20 %, and total exposure at
    90 % of equity.
    """

    MAX_SINGLE_PCT = 12.0
    MAX_LEVERAGED_SINGLE_PCT = 6.0  # Leveraged ETFs get half the single-symbol cap
    MAX_EQUITY_INDEX_ETF_PCT = 20.0
    MAX_TOTAL_PCT = 90.0
    # Broad large-cap index ETFs with heavy megacap overlap.  Treated as a
    # single sleeve so they can't bypass the per-symbol cap by splitting.
    # (Was SPY/VOO; VOO was removed from the universe, QQQ+SPY are now the
    # most overlapping broad-equity pair.)
    EQUITY_INDEX_ETFS = {"SPY", "QQQ"}

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
        max_leveraged = total_equity * self.MAX_LEVERAGED_SINGLE_PCT / 100.0
        max_total = total_equity * self.MAX_TOTAL_PCT / 100.0
        leveraged_etfs = getattr(config, "LEVERAGED_ETFS", set())

        # Cap individual positions (tighter cap for leveraged ETFs)
        for sym in list(target_by_symbol.keys()):
            target_usd = self._get_target_usd(target_by_symbol[sym])
            cap = max_leveraged if sym.upper() in leveraged_etfs else max_single
            cap_pct = self.MAX_LEVERAGED_SINGLE_PCT if sym.upper() in leveraged_etfs else self.MAX_SINGLE_PCT
            if target_usd > cap:
                logger.info(
                    "ExposureLimits: capping %s from $%.2f to $%.2f (%.0f%% of equity%s)",
                    sym, target_usd, cap, cap_pct,
                    " — leveraged ETF" if sym.upper() in leveraged_etfs else "",
                )
                self._set_target_usd(target_by_symbol, sym, cap)

        # SPY and VOO are highly overlapping S&P 500 exposure. Treat them as
        # one sleeve so they cannot bypass the per-symbol cap by splitting.
        max_etf_group = total_equity * self.MAX_EQUITY_INDEX_ETF_PCT / 100.0
        etf_symbols = [sym for sym in target_by_symbol if sym in self.EQUITY_INDEX_ETFS]
        etf_total = sum(self._get_target_usd(target_by_symbol[sym]) for sym in etf_symbols)
        if etf_total > max_etf_group and etf_total > 0:
            scale = max_etf_group / etf_total
            logger.info(
                "ExposureLimits: scaling SPY/VOO sleeve from $%.2f to $%.2f (%.0f%% cap)",
                etf_total, max_etf_group, self.MAX_EQUITY_INDEX_ETF_PCT,
            )
            for sym in etf_symbols:
                self._set_target_usd(
                    target_by_symbol,
                    sym,
                    self._get_target_usd(target_by_symbol[sym]) * scale,
                )

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
    Resets at midnight ET.
    """

    # Conservative limits: survival mode. Reduce to 10 total trades/day
    # and 2 per symbol. Overtrading was a major loss driver.
    MAX_DAILY_TOTAL = int(os.environ.get("MAX_DAILY_TRADES", "10"))
    MAX_DAILY_PER_SYMBOL = int(os.environ.get("MAX_DAILY_PER_SYMBOL", "2"))
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
            "updated_at": _et_now().isoformat(),
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
            if _et_now() - last < timedelta(seconds=self.MIN_REPEAT_SECONDS):
                logger.warning("DuplicateOrderGuard: blocked duplicate %s", key)
                return False
        except Exception:
            return True
        return True

    def record(self, symbol: str, side: str):
        key = f"{symbol}:{side.lower()}"
        self._state[key] = _et_now().isoformat()
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
                if _et_now() - fired_dt < timedelta(days=7):
                    logger.info("CooldownManager: circuit breaker fired within 7 days — multiplier 0.25")
                    return 0.25
            except Exception:
                pass

        # Check daily loss guard (yesterday)
        dl_state = _read_json(DailyLossGuard.STATE_FILE, {})
        yesterday = (_et_now() - timedelta(days=1)).strftime("%Y-%m-%d")
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
            "recorded_at": _et_now().isoformat(),
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
            if _et_now() >= exp_dt:
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

        now = _et_now()
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

    def __init__(self, max_daily_loss_pct: float = 2.0, max_position_loss_pct: float = 8.0):
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

    # ── Trading desk approval ──────────────────────────────────────────
    def approve_trade_request(
        self,
        trade_request: dict,
        account: dict,
        open_positions: list,
        ceo_state=None,
    ) -> dict:
        """
        Approve/reject a manager trade request and compute safe notional size.

        Long-only for now.  Sizing is driven by per-trade risk and the ATR stop
        produced by the selected strategy, then capped by buying power, regime,
        and exposure limits.
        """
        symbol = trade_request.get("symbol", "")
        side = str(trade_request.get("side", "buy")).lower()
        equity = float(account.get("equity", account.get("portfolio_value", 0)) or 0)
        buying_power = float(account.get("buying_power", account.get("cash", 0)) or 0)
        entry_price = float(trade_request.get("entry_price") or 0)
        stop_loss = float(trade_request.get("stop_loss") or 0)
        confidence = float(trade_request.get("confidence") or 0)
        reasons = []

        approval = {
            "approved": False,
            "symbol": symbol,
            "side": side,
            "notional": 0.0,
            "qty_estimate": 0.0,
            "risk_dollars": 0.0,
            "reasons": reasons,
            "trade_request": trade_request,
        }

        if side != "buy":
            reasons.append("Only long entries are enabled in the professional desk engine.")
            return approval
        if equity <= 0 or buying_power <= 0:
            reasons.append("Account equity or buying power is unavailable.")
            return approval

        ok, pre_reasons = self.pre_trade_check(equity)
        if not ok:
            reasons.extend(pre_reasons or ["pre-trade risk check failed"])
            return approval

        if not symbol:
            reasons.append("Missing symbol.")
            return approval

        normalized_positions = {str(p.get("symbol", "")).upper().replace("/", ""): p for p in open_positions or []}
        compact = str(symbol).upper().replace("/", "")
        if compact in normalized_positions:
            reasons.append(f"Duplicate position blocked: {symbol} is already open.")
            return approval

        try:
            from alpaca_client import is_equity_symbol, is_us_market_open
            if is_equity_symbol(symbol) and not is_us_market_open():
                reasons.append("US equity market is closed; stock entries are deferred.")
                return approval
        except Exception:
            pass

        crypto_count = 0
        stock_count = 0
        try:
            from alpaca_client import is_equity_symbol
            for pos in open_positions or []:
                if is_equity_symbol(pos.get("symbol")):
                    stock_count += 1
                else:
                    crypto_count += 1
            if is_equity_symbol(symbol) and stock_count >= config.MAX_CONCURRENT_STOCKS:
                reasons.append(f"Max stock positions reached ({stock_count}/{config.MAX_CONCURRENT_STOCKS}).")
                return approval
            if not is_equity_symbol(symbol) and crypto_count >= config.MAX_CONCURRENT_CRYPTO:
                reasons.append(f"Max crypto positions reached ({crypto_count}/{config.MAX_CONCURRENT_CRYPTO}).")
                return approval
        except Exception:
            pass

        if not self.can_place_order(symbol):
            reasons.append("Trade frequency limit reached.")
            return approval
        if not self.can_submit_order(symbol, side):
            reasons.append("Duplicate order guard blocked this symbol/side.")
            return approval

        if entry_price <= 0 or stop_loss <= 0 or stop_loss >= entry_price:
            reasons.append("Invalid ATR stop geometry; entry must be above stop.")
            return approval

        # Minimum risk:reward check
        rr = float(trade_request.get("risk_reward") or 0)
        min_rr = float(os.environ.get("DESK_MIN_RISK_REWARD", "1.5"))
        if rr > 0 and rr < min_rr:
            reasons.append(
                f"Risk:reward {rr:.2f} is below minimum {min_rr:.2f}."
            )
            return approval

        # Risk per trade: use conservative mode's dynamic percentage if available,
        # otherwise fall back to env var.  In SAFE_TEST_MODE this is 0.15% ($45 on $30K).
        try:
            from conservative_mode import RISK_PER_TRADE_PCT as _cm_risk_pct
            max_risk_pct = _cm_risk_pct
        except Exception:
            max_risk_pct = float(os.environ.get("DESK_RISK_PER_TRADE_PCT", "0.50"))
        base_risk_dollars = equity * max_risk_pct / 100.0
        stop_distance = entry_price - stop_loss
        qty_by_risk = base_risk_dollars / stop_distance if stop_distance > 0 else 0.0
        notional_by_risk = qty_by_risk * entry_price

        regime_mult = float(trade_request.get("ceo_risk_multiplier") or 0.75)
        if ceo_state is not None:
            regime_mult = float(getattr(ceo_state, "risk_multiplier", regime_mult) or regime_mult)
        confidence_mult = max(0.45, min(1.20, 0.45 + confidence))
        cooldown_mult = self.get_exposure_multiplier()

        max_position_pct = float(os.environ.get("DESK_MAX_POSITION_PCT", "8.0"))
        max_position_notional = equity * max_position_pct / 100.0
        notional = min(notional_by_risk, max_position_notional, buying_power * 0.90)
        notional *= max(0.0, regime_mult) * confidence_mult * cooldown_mult

        # Leveraged/inverse ETFs: 50% position size to account for amplified moves
        leveraged_etfs = getattr(config, "LEVERAGED_ETFS", set())
        if symbol.upper() in leveraged_etfs:
            notional *= 0.50
            reasons.append(f"Leveraged ETF {symbol}: position halved for risk control.")

        min_notional = float(os.environ.get("DESK_MIN_ORDER_NOTIONAL", "5.0"))
        if notional < min_notional:
            reasons.append(
                f"Calculated size ${notional:.2f} is below minimum ${min_notional:.2f}; "
                "risk, confidence, buying power, or regime multiplier is too low."
            )
            return approval

        approval.update({
            "approved": True,
            "notional": round(notional, 2),
            "qty_estimate": round(notional / entry_price, 8) if entry_price else 0.0,
            "risk_dollars": round(min(base_risk_dollars, notional * stop_distance / entry_price), 2),
            "reasons": [
                f"Approved: risk ${base_risk_dollars:.2f}, stop distance ${stop_distance:.4f}, "
                f"regime multiplier {regime_mult:.2f}, confidence multiplier {confidence_mult:.2f}."
            ],
        })
        return approval

    # ── Dashboard status ───────────────────────────────────────────────
    def get_status(self) -> dict:
        """Return a dict summarising all risk-control states for the dashboard."""
        now = _et_now()
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
