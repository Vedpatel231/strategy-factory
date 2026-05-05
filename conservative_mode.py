"""
Conservative Profit Mode — Capital preservation first.

This module tracks daily realized P&L, per-asset loss streaks, and
per-strategy loss streaks.  When any threshold is breached, it blocks
new trades for the remainder of the day (UTC).

Two operating modes:
  SAFE_TEST_MODE   — Tiny risk (0.10-0.25%), max 2 open positions, ultra-tight
                     daily limits. Use while proving the system is profitable.
  NORMAL_PAPER_MODE — Standard paper risk (0.50%), normal limits.

All thresholds are PERCENTAGE-BASED and computed dynamically from equity
so the system auto-scales as the account grows or shrinks.

Goals (in priority order):
  1. Do not lose money.
  2. Lock small green profit when achieved.
  3. Only trade when setup quality is genuinely high.

All state resets at midnight UTC.
"""

import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger("conservative_mode")

STATE_FILE = os.path.join(config.DATA_DIR, "conservative_mode.json")


def _utcnow():
    return datetime.now(timezone.utc)


def _today_str():
    return _utcnow().strftime("%Y-%m-%d")


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, STATE_FILE)


# ── Operating Mode ──────────────────────────────────────────────────
#
# SAFE_TEST_MODE (default) — prove the system works before risking real money.
# Set CONSERVATIVE_MODE=normal_paper to switch to standard paper mode.

OPERATING_MODE = os.environ.get("CONSERVATIVE_MODE", "safe_test").lower()
IS_SAFE_TEST = OPERATING_MODE in ("safe_test", "safe_test_mode", "safe")


# ── Percentage-based thresholds (dynamic from equity) ───────────────
#
# These percentages are applied to CURRENT EQUITY each day.
# Example on $30,000 account in safe_test mode:
#   daily profit target  = 0.50% = $150
#   daily loss limit     = 0.67% = $200
#   green protection     = 0.25% = $75
#   risk per trade       = 0.15% = $45
#   max open risk budget = 0.50% = $150

# --- Daily limits (% of equity) ---
DAILY_PROFIT_TARGET_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_DAILY_PROFIT_TARGET_PCT"),
    0.50 if IS_SAFE_TEST else 1.00,
)
DAILY_LOSS_LIMIT_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_DAILY_LOSS_LIMIT_PCT"),
    0.67 if IS_SAFE_TEST else 1.50,
)
GREEN_PROTECTION_START_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_GREEN_PROTECTION_PCT"),
    0.25 if IS_SAFE_TEST else 0.50,
)

# --- Per-trade risk (% of equity) ---
# SAFE_TEST: 0.10% to 0.25% — absolute maximum $75 on a $30K account.
# NORMAL:    0.50% — standard paper risk, ~$150 on $30K.
RISK_PER_TRADE_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_RISK_PER_TRADE_PCT"),
    0.15 if IS_SAFE_TEST else 0.50,
)

# --- Open risk budget (% of equity) ---
# Total risk across ALL open positions cannot exceed this.
# With 0.15% per trade and 2 max positions, the cap is 0.50%.
MAX_OPEN_RISK_BUDGET_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_MAX_OPEN_RISK_PCT"),
    0.50 if IS_SAFE_TEST else 1.50,
)

# --- Max open positions ---
MAX_OPEN_POSITIONS = int(
    os.environ.get("CONSERVATIVE_MAX_OPEN_POSITIONS",
                    "2" if IS_SAFE_TEST else "4")
)

# --- Max trades per day ---
MAX_TRADES_PER_DAY = int(
    os.environ.get("CONSERVATIVE_MAX_TRADES_PER_DAY",
                    "5" if IS_SAFE_TEST else "10")
)

# Per-asset: pause after N consecutive losses on the same asset today.
ASSET_CONSECUTIVE_LOSS_LIMIT = int(
    os.environ.get("CONSERVATIVE_ASSET_LOSS_LIMIT", "2")
)

# Per-strategy: disable after N consecutive losses globally.
STRATEGY_CONSECUTIVE_LOSS_LIMIT = int(
    os.environ.get("CONSERVATIVE_STRATEGY_LOSS_LIMIT", "3")
)

# Minimum risk:reward ratio to allow a trade.
MIN_RISK_REWARD = _safe_float(
    os.environ.get("CONSERVATIVE_MIN_RR"), 1.5
)

# When in green protection, require this higher confidence boost.
GREEN_CONFIDENCE_BOOST = _safe_float(
    os.environ.get("CONSERVATIVE_GREEN_CONF_BOOST"), 0.08
)


class ConservativeMode:
    """Daily P&L tracker, open risk budget, and trade gate.

    Call `set_equity()` at the start of each cycle with current account equity.
    Call `record_trade_result()` after every closed trade.
    Call `register_open_risk()` when a new position is opened.
    Call `release_open_risk()` when a position is closed.
    Call `can_trade()` before every new entry.
    State persists to disk and resets at midnight UTC.
    """

    def __init__(self, equity=None):
        self._load()
        if equity and equity > 0:
            self.set_equity(equity)

    def _load(self):
        raw = _read_state()
        today = _today_str()
        if raw.get("date") != today:
            self._state = self._fresh_state(today)
            self._persist()
        else:
            self._state = raw

    def _fresh_state(self, date):
        return {
            "date": date,
            "operating_mode": "safe_test" if IS_SAFE_TEST else "normal_paper",
            "equity": 0.0,
            "realized_pl": 0.0,
            "unrealized_pl": 0.0,
            "trades_today": 0,
            "wins_today": 0,
            "losses_today": 0,
            "daily_locked": False,
            "lock_reason": "",
            "asset_streaks": {},
            "strategy_streaks": {},
            "paused_assets": [],
            "disabled_strategies": [],
            "green_protection": False,
            "trade_log": [],
            # Open risk budget tracking
            "open_risk_slots": {},    # symbol -> risk_dollars at entry
            "total_open_risk": 0.0,
            # Dynamic dollar thresholds (computed from equity)
            "daily_profit_target_usd": 0.0,
            "daily_loss_limit_usd": 0.0,
            "green_protection_usd": 0.0,
            "risk_per_trade_usd": 0.0,
            "max_open_risk_usd": 0.0,
        }

    def _persist(self):
        _write_state(self._state)

    def _maybe_reset(self):
        """Reset if the date rolled over."""
        today = _today_str()
        if self._state.get("date") != today:
            old_strat_streaks = self._state.get("strategy_streaks", {})
            old_disabled = self._state.get("disabled_strategies", [])
            self._state = self._fresh_state(today)
            self._state["strategy_streaks"] = old_strat_streaks
            self._state["disabled_strategies"] = old_disabled
            self._persist()

    # ── Equity and dynamic thresholds ────────────────────────────────

    def set_equity(self, equity):
        """Set current equity and recompute all dollar thresholds.

        Must be called at the start of each trading cycle so all limits
        scale dynamically with the account size.
        """
        self._maybe_reset()
        equity = _safe_float(equity, 0.0)
        if equity <= 0:
            return
        self._state["equity"] = round(equity, 2)
        self._state["daily_profit_target_usd"] = round(equity * DAILY_PROFIT_TARGET_PCT / 100.0, 2)
        self._state["daily_loss_limit_usd"] = round(-equity * DAILY_LOSS_LIMIT_PCT / 100.0, 2)
        self._state["green_protection_usd"] = round(equity * GREEN_PROTECTION_START_PCT / 100.0, 2)
        self._state["risk_per_trade_usd"] = round(equity * RISK_PER_TRADE_PCT / 100.0, 2)
        self._state["max_open_risk_usd"] = round(equity * MAX_OPEN_RISK_BUDGET_PCT / 100.0, 2)
        self._persist()

    def get_risk_per_trade(self):
        """Return the max risk dollars for a single trade."""
        return _safe_float(self._state.get("risk_per_trade_usd"), 0.0)

    def get_risk_per_trade_pct(self):
        """Return the per-trade risk percentage."""
        return RISK_PER_TRADE_PCT

    # ── Open risk budget ─────────────────────────────────────────────

    def register_open_risk(self, symbol, risk_dollars):
        """Record risk committed when a new position is opened."""
        self._maybe_reset()
        risk_dollars = round(_safe_float(risk_dollars), 2)
        slots = self._state.get("open_risk_slots", {})
        slots[symbol] = risk_dollars
        self._state["open_risk_slots"] = slots
        self._state["total_open_risk"] = round(
            sum(slots.values()), 2
        )
        self._persist()
        logger.info(
            "ConservativeMode: registered open risk $%.2f for %s "
            "(total open risk: $%.2f / $%.2f)",
            risk_dollars, symbol,
            self._state["total_open_risk"],
            self._state.get("max_open_risk_usd", 0),
        )

    def release_open_risk(self, symbol):
        """Release risk budget when a position is closed."""
        self._maybe_reset()
        slots = self._state.get("open_risk_slots", {})
        released = slots.pop(symbol, 0.0)
        self._state["open_risk_slots"] = slots
        self._state["total_open_risk"] = round(
            sum(slots.values()), 2
        )
        self._persist()
        if released:
            logger.info(
                "ConservativeMode: released $%.2f risk for %s "
                "(total open risk: $%.2f)",
                released, symbol, self._state["total_open_risk"],
            )

    # ── Record trade result ──────────────────────────────────────────

    def record_trade_result(self, symbol, strategy, net_pl, reason=""):
        """Call after every closed trade (including partial exits)."""
        self._maybe_reset()
        self._state["realized_pl"] = round(
            self._state["realized_pl"] + _safe_float(net_pl), 2
        )
        self._state["trades_today"] += 1

        is_win = net_pl > 0
        if is_win:
            self._state["wins_today"] += 1
            self._state["asset_streaks"][symbol] = 0
            self._state["strategy_streaks"][strategy] = 0
        else:
            self._state["losses_today"] += 1
            self._state["asset_streaks"][symbol] = (
                self._state["asset_streaks"].get(symbol, 0) + 1
            )
            self._state["strategy_streaks"][strategy] = (
                self._state["strategy_streaks"].get(strategy, 0) + 1
            )

        # Check asset pause
        if self._state["asset_streaks"].get(symbol, 0) >= ASSET_CONSECUTIVE_LOSS_LIMIT:
            if symbol not in self._state["paused_assets"]:
                self._state["paused_assets"].append(symbol)
                logger.warning(
                    "ConservativeMode: %s paused for the day after %d consecutive losses",
                    symbol, ASSET_CONSECUTIVE_LOSS_LIMIT,
                )

        # Check strategy disable
        if self._state["strategy_streaks"].get(strategy, 0) >= STRATEGY_CONSECUTIVE_LOSS_LIMIT:
            if strategy not in self._state["disabled_strategies"]:
                self._state["disabled_strategies"].append(strategy)
                logger.warning(
                    "ConservativeMode: strategy '%s' disabled after %d consecutive losses",
                    strategy, STRATEGY_CONSECUTIVE_LOSS_LIMIT,
                )

        # Check daily limits (dynamic)
        profit_target = self._state.get("daily_profit_target_usd", 0)
        loss_limit = self._state.get("daily_loss_limit_usd", 0)

        if profit_target > 0 and self._state["realized_pl"] >= profit_target:
            self._state["daily_locked"] = True
            self._state["lock_reason"] = (
                f"Daily profit target reached: ${self._state['realized_pl']:.2f} "
                f">= ${profit_target:.2f} ({DAILY_PROFIT_TARGET_PCT:.2f}% of equity)"
            )
            logger.info("ConservativeMode: %s", self._state["lock_reason"])
            self._send_alert(self._state["lock_reason"])

        if loss_limit < 0 and self._state["realized_pl"] <= loss_limit:
            self._state["daily_locked"] = True
            self._state["lock_reason"] = (
                f"Daily loss limit hit: ${self._state['realized_pl']:.2f} "
                f"<= ${loss_limit:.2f} ({DAILY_LOSS_LIMIT_PCT:.2f}% of equity)"
            )
            logger.warning("ConservativeMode: %s", self._state["lock_reason"])
            self._send_alert(self._state["lock_reason"])

        # Green protection
        green_threshold = self._state.get("green_protection_usd", 0)
        if green_threshold > 0 and self._state["realized_pl"] >= green_threshold:
            self._state["green_protection"] = True

        # Append to trade log
        self._state["trade_log"].append({
            "symbol": symbol,
            "strategy": strategy,
            "net_pl": round(net_pl, 2),
            "cumulative_pl": self._state["realized_pl"],
            "timestamp": _utcnow().isoformat(),
            "reason": reason[:120],
        })
        self._state["trade_log"] = self._state["trade_log"][-50:]
        self._persist()

    # ── Update unrealized P&L ────────────────────────────────────────

    def update_unrealized(self, unrealized_pl):
        """Call each cycle with total unrealized P&L from open positions."""
        self._maybe_reset()
        self._state["unrealized_pl"] = round(_safe_float(unrealized_pl), 2)
        combined = self._state["realized_pl"] + self._state["unrealized_pl"]
        loss_limit = self._state.get("daily_loss_limit_usd", 0)

        if loss_limit < 0 and combined <= loss_limit and not self._state["daily_locked"]:
            self._state["daily_locked"] = True
            self._state["lock_reason"] = (
                f"Combined P&L hit daily loss limit: ${combined:.2f} "
                f"(realized ${self._state['realized_pl']:.2f} + "
                f"unrealized ${self._state['unrealized_pl']:.2f}) "
                f"<= ${loss_limit:.2f}"
            )
            logger.warning("ConservativeMode: %s", self._state["lock_reason"])
            self._send_alert(self._state["lock_reason"])

        self._persist()

    # ── Can trade? ───────────────────────────────────────────────────

    def can_trade(self, symbol=None, strategy=None, risk_reward=0.0,
                  proposed_risk_dollars=0.0, open_position_count=0):
        """
        Return (allowed: bool, reason: str).
        Must pass ALL gates to trade.
        """
        self._maybe_reset()

        # Gate 1: Daily lock
        if self._state.get("daily_locked"):
            return False, f"Trading locked for today: {self._state.get('lock_reason', 'daily limit')}"

        # Gate 2: Max trades per day
        if self._state.get("trades_today", 0) >= MAX_TRADES_PER_DAY:
            return False, (
                f"Max trades per day reached: {self._state['trades_today']} >= {MAX_TRADES_PER_DAY}"
            )

        # Gate 3: Max open positions
        if open_position_count >= MAX_OPEN_POSITIONS:
            return False, (
                f"Max open positions reached: {open_position_count} >= {MAX_OPEN_POSITIONS}"
            )

        # Gate 4: Asset paused
        if symbol and symbol in self._state.get("paused_assets", []):
            streak = self._state.get("asset_streaks", {}).get(symbol, 0)
            return False, (
                f"{symbol} paused for today after {streak} consecutive losses"
            )

        # Gate 5: Strategy disabled
        if strategy and strategy in self._state.get("disabled_strategies", []):
            streak = self._state.get("strategy_streaks", {}).get(strategy, 0)
            return False, (
                f"Strategy '{strategy}' disabled after {streak} consecutive losses"
            )

        # Gate 6: Minimum R:R
        if risk_reward > 0 and risk_reward < MIN_RISK_REWARD:
            return False, (
                f"Risk:reward {risk_reward:.2f} below minimum {MIN_RISK_REWARD:.2f}"
            )

        # Gate 7: Green protection — tighter R:R when we're already green
        if self._state.get("green_protection") and risk_reward > 0:
            green_rr = MIN_RISK_REWARD + 0.5
            if risk_reward < green_rr:
                return False, (
                    f"Green protection active: R:R {risk_reward:.2f} below "
                    f"protected minimum {green_rr:.2f}"
                )

        # Gate 8: Open risk budget
        if proposed_risk_dollars > 0:
            max_open_risk = self._state.get("max_open_risk_usd", 0)
            current_open_risk = self._state.get("total_open_risk", 0)
            if max_open_risk > 0:
                new_total = current_open_risk + proposed_risk_dollars
                if new_total > max_open_risk:
                    return False, (
                        f"Open risk budget exceeded: "
                        f"current ${current_open_risk:.2f} + proposed ${proposed_risk_dollars:.2f} "
                        f"= ${new_total:.2f} > budget ${max_open_risk:.2f} "
                        f"({MAX_OPEN_RISK_BUDGET_PCT:.2f}% of equity)"
                    )

        # Gate 9: Per-trade risk cap
        if proposed_risk_dollars > 0:
            max_per_trade = self._state.get("risk_per_trade_usd", 0)
            if max_per_trade > 0 and proposed_risk_dollars > max_per_trade * 1.05:
                return False, (
                    f"Per-trade risk ${proposed_risk_dollars:.2f} exceeds maximum "
                    f"${max_per_trade:.2f} ({RISK_PER_TRADE_PCT:.2f}% of equity)"
                )

        return True, "Trade allowed"

    def get_confidence_boost(self):
        """Return extra confidence required when in green protection mode."""
        self._maybe_reset()
        if self._state.get("green_protection"):
            return GREEN_CONFIDENCE_BOOST
        return 0.0

    # ── Status for dashboard ─────────────────────────────────────────

    def get_status(self):
        """Return full state for dashboard display."""
        self._maybe_reset()
        return {
            "mode": "SAFE_TEST_MODE" if IS_SAFE_TEST else "NORMAL_PAPER_MODE",
            "operating_mode": self._state.get("operating_mode", "safe_test"),
            "date": self._state.get("date"),
            "equity": self._state.get("equity", 0),
            "realized_pl": self._state.get("realized_pl", 0),
            "unrealized_pl": self._state.get("unrealized_pl", 0),
            "combined_pl": round(
                self._state.get("realized_pl", 0) + self._state.get("unrealized_pl", 0), 2
            ),
            # Dynamic dollar thresholds
            "daily_profit_target": self._state.get("daily_profit_target_usd", 0),
            "daily_loss_limit": self._state.get("daily_loss_limit_usd", 0),
            "green_protection_threshold": self._state.get("green_protection_usd", 0),
            "risk_per_trade": self._state.get("risk_per_trade_usd", 0),
            # Percentage configs
            "daily_profit_target_pct": DAILY_PROFIT_TARGET_PCT,
            "daily_loss_limit_pct": DAILY_LOSS_LIMIT_PCT,
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "max_open_risk_pct": MAX_OPEN_RISK_BUDGET_PCT,
            # Daily state
            "daily_locked": self._state.get("daily_locked", False),
            "lock_reason": self._state.get("lock_reason", ""),
            "trades_today": self._state.get("trades_today", 0),
            "max_trades_per_day": MAX_TRADES_PER_DAY,
            "wins_today": self._state.get("wins_today", 0),
            "losses_today": self._state.get("losses_today", 0),
            "win_rate_today": round(
                self._state.get("wins_today", 0) /
                max(1, self._state.get("trades_today", 1)) * 100, 1
            ),
            "green_protection": self._state.get("green_protection", False),
            # Open risk budget
            "total_open_risk": self._state.get("total_open_risk", 0),
            "max_open_risk": self._state.get("max_open_risk_usd", 0),
            "open_risk_slots": self._state.get("open_risk_slots", {}),
            "max_open_positions": MAX_OPEN_POSITIONS,
            "current_open_positions": len(self._state.get("open_risk_slots", {})),
            # Streaks and pauses
            "paused_assets": self._state.get("paused_assets", []),
            "disabled_strategies": self._state.get("disabled_strategies", []),
            "asset_streaks": self._state.get("asset_streaks", {}),
            "strategy_streaks": self._state.get("strategy_streaks", {}),
            "min_risk_reward": MIN_RISK_REWARD,
            "trade_log": self._state.get("trade_log", [])[-10:],
        }

    # ── Alerts ───────────────────────────────────────────────────────

    def _send_alert(self, message):
        try:
            from telegram_notifier import send_alert, is_configured
            if is_configured():
                mode_label = "SAFE TEST" if IS_SAFE_TEST else "PAPER"
                send_alert(
                    f"🛡️ CONSERVATIVE MODE [{mode_label}]\n\n{message}",
                    level="warning",
                )
        except Exception:
            pass

    # ── Manual controls ──────────────────────────────────────────────

    def reset_strategy(self, strategy):
        """Re-enable a strategy that was auto-disabled."""
        self._maybe_reset()
        if strategy in self._state.get("disabled_strategies", []):
            self._state["disabled_strategies"].remove(strategy)
        self._state["strategy_streaks"][strategy] = 0
        self._persist()

    def reset_asset(self, symbol):
        """Re-enable an asset that was auto-paused."""
        self._maybe_reset()
        if symbol in self._state.get("paused_assets", []):
            self._state["paused_assets"].remove(symbol)
        self._state["asset_streaks"][symbol] = 0
        self._persist()

    def force_lock(self, reason="Manual lock"):
        """Manually lock trading for the rest of the day."""
        self._maybe_reset()
        self._state["daily_locked"] = True
        self._state["lock_reason"] = reason
        self._persist()

    def force_unlock(self):
        """Manually unlock trading (use with caution)."""
        self._maybe_reset()
        self._state["daily_locked"] = False
        self._state["lock_reason"] = ""
        self._persist()
