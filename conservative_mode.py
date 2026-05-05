"""
Conservative Profit Mode — Capital preservation first.

This module tracks daily realized P&L, per-asset loss streaks, and
per-strategy loss streaks.  When any threshold is breached, it blocks
new trades for the remainder of the day (UTC).

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


# ── Account-aware thresholds ─────────────────────────────────────────
#
# Defaults are calibrated for a ~$30,000 paper account:
#   - 0.5% risk per trade ≈ $150 risk per position
#   - Typical win at 2:1 R:R ≈ $300
#   - Typical loss ≈ $150
#   - Daily target = ~0.5% of account = $150
#   - Daily loss limit = ~0.67% of account = $200
#
# These allow 3-5 trades to play out before locking.
# Override via env vars for different account sizes.

# Daily profit target: once realized P&L reaches this, stop trading.
# $150 on a $30K account = one solid winning trade locks the day green.
DAILY_PROFIT_TARGET = _safe_float(
    os.environ.get("CONSERVATIVE_DAILY_PROFIT_TARGET"), 150.00
)

# Daily loss limit: once realized + unrealized P&L hits this, stop.
# -$200 on a $30K account = about 1.3 losing trades before lockout.
DAILY_LOSS_LIMIT = _safe_float(
    os.environ.get("CONSERVATIVE_DAILY_LOSS_LIMIT"), -200.00
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

# Green protection: once daily P&L is positive by this much,
# raise confidence threshold and tighten R:R requirement.
# $75 = half a winning trade — enough to know the day is working.
GREEN_PROTECTION_THRESHOLD = _safe_float(
    os.environ.get("CONSERVATIVE_GREEN_PROTECTION"), 75.00
)

# When in green protection, require this higher confidence.
GREEN_CONFIDENCE_BOOST = _safe_float(
    os.environ.get("CONSERVATIVE_GREEN_CONF_BOOST"), 0.08
)


class ConservativeMode:
    """Daily P&L tracker and trade gate.

    Call `record_trade_result()` after every closed trade.
    Call `can_trade()` before every new entry.
    State persists to disk and resets at midnight UTC.
    """

    def __init__(self):
        self._load()

    def _load(self):
        raw = _read_state()
        today = _today_str()
        if raw.get("date") != today:
            # New day — fresh state
            self._state = self._fresh_state(today)
            self._persist()
        else:
            self._state = raw

    def _fresh_state(self, date):
        return {
            "date": date,
            "realized_pl": 0.0,
            "unrealized_pl": 0.0,
            "trades_today": 0,
            "wins_today": 0,
            "losses_today": 0,
            "daily_locked": False,
            "lock_reason": "",
            "asset_streaks": {},       # symbol -> consecutive losses today
            "strategy_streaks": {},    # strategy -> consecutive losses
            "paused_assets": [],       # symbols paused for the day
            "disabled_strategies": [], # strategies disabled until review
            "green_protection": False,
            "trade_log": [],           # brief log of today's trades
        }

    def _persist(self):
        _write_state(self._state)

    def _maybe_reset(self):
        """Reset if the date rolled over."""
        today = _today_str()
        if self._state.get("date") != today:
            # Keep strategy streaks across days (they only reset on a win)
            old_strat_streaks = self._state.get("strategy_streaks", {})
            old_disabled = self._state.get("disabled_strategies", [])
            self._state = self._fresh_state(today)
            self._state["strategy_streaks"] = old_strat_streaks
            self._state["disabled_strategies"] = old_disabled
            self._persist()

    # ── Record trade result ───────────────────────────────────────────

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
            # Reset streaks on win
            self._state["asset_streaks"][symbol] = 0
            self._state["strategy_streaks"][strategy] = 0
        else:
            self._state["losses_today"] += 1
            # Increment consecutive loss streaks
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

        # Check daily limits
        if self._state["realized_pl"] >= DAILY_PROFIT_TARGET:
            self._state["daily_locked"] = True
            self._state["lock_reason"] = (
                f"Daily profit target reached: ${self._state['realized_pl']:.2f} >= ${DAILY_PROFIT_TARGET:.2f}"
            )
            logger.info("ConservativeMode: %s", self._state["lock_reason"])
            self._send_alert(self._state["lock_reason"])

        if self._state["realized_pl"] <= DAILY_LOSS_LIMIT:
            self._state["daily_locked"] = True
            self._state["lock_reason"] = (
                f"Daily loss limit hit: ${self._state['realized_pl']:.2f} <= ${DAILY_LOSS_LIMIT:.2f}"
            )
            logger.warning("ConservativeMode: %s", self._state["lock_reason"])
            self._send_alert(self._state["lock_reason"])

        # Green protection
        if self._state["realized_pl"] >= GREEN_PROTECTION_THRESHOLD:
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
        # Keep log manageable
        self._state["trade_log"] = self._state["trade_log"][-50:]

        self._persist()

    # ── Update unrealized P&L ─────────────────────────────────────────

    def update_unrealized(self, unrealized_pl):
        """Call each cycle with total unrealized P&L from open positions."""
        self._maybe_reset()
        self._state["unrealized_pl"] = round(_safe_float(unrealized_pl), 2)
        combined = self._state["realized_pl"] + self._state["unrealized_pl"]

        if combined <= DAILY_LOSS_LIMIT and not self._state["daily_locked"]:
            self._state["daily_locked"] = True
            self._state["lock_reason"] = (
                f"Combined P&L hit daily loss limit: ${combined:.2f} "
                f"(realized ${self._state['realized_pl']:.2f} + "
                f"unrealized ${self._state['unrealized_pl']:.2f})"
            )
            logger.warning("ConservativeMode: %s", self._state["lock_reason"])
            self._send_alert(self._state["lock_reason"])

        self._persist()

    # ── Can trade? ────────────────────────────────────────────────────

    def can_trade(self, symbol=None, strategy=None, risk_reward=0.0):
        """
        Return (allowed: bool, reason: str).
        Must pass ALL gates to trade.
        """
        self._maybe_reset()

        # Gate 1: Daily lock
        if self._state.get("daily_locked"):
            return False, f"Trading locked for today: {self._state.get('lock_reason', 'daily limit')}"

        # Gate 2: Asset paused
        if symbol and symbol in self._state.get("paused_assets", []):
            streak = self._state.get("asset_streaks", {}).get(symbol, 0)
            return False, (
                f"{symbol} paused for today after {streak} consecutive losses"
            )

        # Gate 3: Strategy disabled
        if strategy and strategy in self._state.get("disabled_strategies", []):
            streak = self._state.get("strategy_streaks", {}).get(strategy, 0)
            return False, (
                f"Strategy '{strategy}' disabled after {streak} consecutive losses"
            )

        # Gate 4: Minimum R:R
        if risk_reward > 0 and risk_reward < MIN_RISK_REWARD:
            return False, (
                f"Risk:reward {risk_reward:.2f} below minimum {MIN_RISK_REWARD:.2f}"
            )

        # Gate 5: Green protection — tighter R:R when we're already green
        if self._state.get("green_protection") and risk_reward > 0:
            green_rr = MIN_RISK_REWARD + 0.5  # e.g. 2.0 when protecting green
            if risk_reward < green_rr:
                return False, (
                    f"Green protection active: R:R {risk_reward:.2f} below "
                    f"protected minimum {green_rr:.2f}"
                )

        return True, "Trade allowed"

    def get_confidence_boost(self):
        """Return extra confidence required when in green protection mode."""
        self._maybe_reset()
        if self._state.get("green_protection"):
            return GREEN_CONFIDENCE_BOOST
        return 0.0

    # ── Status for dashboard ──────────────────────────────────────────

    def get_status(self):
        """Return full state for dashboard display."""
        self._maybe_reset()
        return {
            "mode": "CONSERVATIVE_PROFIT_MODE",
            "date": self._state.get("date"),
            "realized_pl": self._state.get("realized_pl", 0),
            "unrealized_pl": self._state.get("unrealized_pl", 0),
            "combined_pl": round(
                self._state.get("realized_pl", 0) + self._state.get("unrealized_pl", 0), 2
            ),
            "daily_profit_target": DAILY_PROFIT_TARGET,
            "daily_loss_limit": DAILY_LOSS_LIMIT,
            "daily_locked": self._state.get("daily_locked", False),
            "lock_reason": self._state.get("lock_reason", ""),
            "trades_today": self._state.get("trades_today", 0),
            "wins_today": self._state.get("wins_today", 0),
            "losses_today": self._state.get("losses_today", 0),
            "win_rate_today": round(
                self._state.get("wins_today", 0) /
                max(1, self._state.get("trades_today", 1)) * 100, 1
            ),
            "green_protection": self._state.get("green_protection", False),
            "paused_assets": self._state.get("paused_assets", []),
            "disabled_strategies": self._state.get("disabled_strategies", []),
            "asset_streaks": self._state.get("asset_streaks", {}),
            "strategy_streaks": self._state.get("strategy_streaks", {}),
            "min_risk_reward": MIN_RISK_REWARD,
            "trade_log": self._state.get("trade_log", [])[-10:],
        }

    # ── Alerts ────────────────────────────────────────────────────────

    def _send_alert(self, message):
        try:
            from telegram_notifier import send_alert, is_configured
            if is_configured():
                send_alert(f"🛡️ CONSERVATIVE MODE\n\n{message}", level="warning")
        except Exception:
            pass

    # ── Manual controls ───────────────────────────────────────────────

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
