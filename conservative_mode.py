"""
Conservative Profit Mode — Capital preservation first.

Daily behaviour is governed by NET P&L (gross P&L minus estimated fees and
slippage), NOT by a fixed maximum-trades-per-day count.

Three dynamic daily modes:

  SAFE_TEST_MODE (default)
      Net P&L between -0.5 % and +1 % of equity.
      Trade quality threshold: 75.  Normal risk per trade.

  PROFIT_PROTECTION_MODE
      Net P&L >= +1 % of equity.
      Trade quality threshold: 90.  Only near-perfect setups.
      Higher R:R minimum (2.0).  Reduced position sizing.
      Goal: protect the green day, don't give back gains.

  LOSS_RECOVERY_PROTECTION_MODE
      Net P&L <= -0.5 % of equity.
      Trade quality threshold: 90.  Only excellent setups.
      No revenge trading, no chasing.
      Goal: allow one great setup to reduce the loss, or end the day.

All thresholds are PERCENTAGE-BASED and computed dynamically from live
Alpaca equity each cycle.

Daily net P&L includes:
  - Realized P&L (closed trades)
  - Unrealized P&L (open positions)
  - Estimated trading fees / commissions
  - Estimated slippage

State resets at midnight UTC.
"""

import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger("conservative_mode")

STATE_FILE = os.path.join(config.DATA_DIR, "conservative_mode.json")

# ── Daily mode names (constants) ────────────────────────────────────
MODE_SAFE_TEST = "SAFE_TEST_MODE"
MODE_PROFIT_PROTECTION = "PROFIT_PROTECTION_MODE"
MODE_LOSS_RECOVERY = "LOSS_RECOVERY_PROTECTION_MODE"


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


# ── Percentage-based thresholds (env-overridable) ───────────────────
#
# All computed from CURRENT EQUITY each cycle.  $30,000 examples:
#   profit threshold    = 1.00 % = +$300
#   loss threshold      = 0.50 % = -$150
#   risk per trade      = 0.15 % =  $45
#   max open risk       = 0.50 % = $150

# --- Daily P&L thresholds (% of equity) ---
DAILY_PROFIT_THRESHOLD_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_DAILY_PROFIT_THRESHOLD_PCT"), 1.00
)
DAILY_LOSS_THRESHOLD_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_DAILY_LOSS_THRESHOLD_PCT"), 0.50
)

# --- Per-trade risk (% of equity) ---
RISK_PER_TRADE_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_RISK_PER_TRADE_PCT"), 0.15
)

# --- Open risk budget (% of equity) ---
MAX_OPEN_RISK_BUDGET_PCT = _safe_float(
    os.environ.get("CONSERVATIVE_MAX_OPEN_RISK_PCT"), 0.50
)

# --- Max open positions ---
# With 100 stocks + 3 crypto, allow up to 5 concurrent positions
MAX_OPEN_POSITIONS = int(
    os.environ.get("CONSERVATIVE_MAX_OPEN_POSITIONS", "5")
)

# --- Fee / slippage estimation ---
# Different fee models for stocks vs crypto:
#   Stocks: $0 commission on Alpaca, ~0.01% slippage estimate
#   Crypto: ~0.15% maker / 0.25% taker spread + slippage
EST_FEE_PCT_STOCK = _safe_float(
    os.environ.get("CONSERVATIVE_EST_FEE_PCT_STOCK"), 0.01
)
EST_FEE_PCT_CRYPTO = _safe_float(
    os.environ.get("CONSERVATIVE_EST_FEE_PCT_CRYPTO"), 0.20
)
# Legacy default (used if asset class unknown)
EST_FEE_PCT_PER_TRADE = _safe_float(
    os.environ.get("CONSERVATIVE_EST_FEE_PCT"), 0.10
)

# Per-asset: pause after N consecutive losses on the same asset today.
ASSET_CONSECUTIVE_LOSS_LIMIT = int(
    os.environ.get("CONSERVATIVE_ASSET_LOSS_LIMIT", "2")
)

# Per-strategy: disable after N consecutive losses globally.
STRATEGY_CONSECUTIVE_LOSS_LIMIT = int(
    os.environ.get("CONSERVATIVE_STRATEGY_LOSS_LIMIT", "3")
)

# Minimum risk:reward ratio — normal mode.
MIN_RISK_REWARD_NORMAL = _safe_float(
    os.environ.get("CONSERVATIVE_MIN_RR"), 1.5
)

# Minimum R:R in protection modes (profit or loss threshold reached).
MIN_RISK_REWARD_PROTECTION = _safe_float(
    os.environ.get("CONSERVATIVE_MIN_RR_PROTECTION"), 2.0
)

# Trade quality thresholds per daily mode.
QUALITY_SCORE_NORMAL = int(
    os.environ.get("CONSERVATIVE_QUALITY_NORMAL", "75")
)
QUALITY_SCORE_PROTECTION = int(
    os.environ.get("CONSERVATIVE_QUALITY_PROTECTION", "90")
)

# Adaptive quality thresholds based on CEO posture.
# These override QUALITY_SCORE_NORMAL when the market is clear.
QUALITY_SCORE_RISK_ON = int(
    os.environ.get("CONSERVATIVE_QUALITY_RISK_ON", "72")
)
QUALITY_SCORE_CHOPPY = int(
    os.environ.get("CONSERVATIVE_QUALITY_CHOPPY", "85")
)

# When in green protection, require this higher confidence boost.
GREEN_CONFIDENCE_BOOST = _safe_float(
    os.environ.get("CONSERVATIVE_GREEN_CONF_BOOST"), 0.08
)


class ConservativeMode:
    """Daily NET P&L tracker, dynamic mode switcher, and trade gate.

    Lifecycle per cycle:
      1. ``set_equity(equity)``  — recompute dollar thresholds
      2. ``record_trade_result()`` — after each closed / partial trade
      3. ``record_fees(amount)``   — accumulate estimated fees
      4. ``update_unrealized(upl)`` — set current unrealized P&L
      5. ``get_daily_mode()``      — returns current mode name
      6. ``can_trade(...)``        — pre-entry gate
      7. ``get_required_quality_score()`` — for asset manager quality gate
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
            "equity": 0.0,
            # Gross P&L components
            "realized_pl": 0.0,
            "unrealized_pl": 0.0,
            # Fee / slippage tracking
            "estimated_fees": 0.0,
            "trade_count_for_fees": 0,
            # Net P&L = realized + unrealized - fees
            "net_daily_pl": 0.0,
            # Trade counters
            "trades_today": 0,
            "wins_today": 0,
            "losses_today": 0,
            # Daily mode (recomputed each cycle)
            "daily_mode": MODE_SAFE_TEST,
            "daily_mode_reason": "",
            # Streaks and pauses
            "asset_streaks": {},
            "strategy_streaks": {},
            "paused_assets": [],
            "disabled_strategies": [],
            # Open risk budget
            "open_risk_slots": {},
            "total_open_risk": 0.0,
            # Dynamic dollar thresholds (set by set_equity)
            "profit_threshold_usd": 0.0,
            "loss_threshold_usd": 0.0,
            "risk_per_trade_usd": 0.0,
            "max_open_risk_usd": 0.0,
            # Trade log
            "trade_log": [],
        }

    def _persist(self):
        _write_state(self._state)

    def _maybe_reset(self):
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
        """Set current equity and recompute all dollar thresholds."""
        self._maybe_reset()
        equity = _safe_float(equity, 0.0)
        if equity <= 0:
            return
        self._state["equity"] = round(equity, 2)
        self._state["profit_threshold_usd"] = round(
            equity * DAILY_PROFIT_THRESHOLD_PCT / 100.0, 2
        )
        self._state["loss_threshold_usd"] = round(
            -equity * DAILY_LOSS_THRESHOLD_PCT / 100.0, 2
        )
        self._state["risk_per_trade_usd"] = round(
            equity * RISK_PER_TRADE_PCT / 100.0, 2
        )
        self._state["max_open_risk_usd"] = round(
            equity * MAX_OPEN_RISK_BUDGET_PCT / 100.0, 2
        )
        self._recompute_daily_mode()
        self._persist()

    def get_risk_per_trade(self):
        return _safe_float(self._state.get("risk_per_trade_usd"), 0.0)

    def get_risk_per_trade_pct(self):
        return RISK_PER_TRADE_PCT

    # ── Fee / slippage tracking ──────────────────────────────────────

    def record_fees(self, fee_amount):
        """Add estimated fees/slippage for a trade.

        Called by trading_desk after each order execution with the
        estimated round-trip cost.
        """
        self._maybe_reset()
        self._state["estimated_fees"] = round(
            self._state.get("estimated_fees", 0) + _safe_float(fee_amount), 2
        )
        self._state["trade_count_for_fees"] = (
            self._state.get("trade_count_for_fees", 0) + 1
        )
        self._recompute_daily_mode()
        self._persist()

    def estimate_fee_for_notional(self, notional, asset_class=None):
        """Return estimated round-trip fee for a given notional amount.

        Uses different rates for stocks ($0 commission, minimal slippage)
        vs crypto (0.20% spread + slippage).
        """
        if asset_class == "stock":
            fee_pct = EST_FEE_PCT_STOCK
        elif asset_class == "crypto":
            fee_pct = EST_FEE_PCT_CRYPTO
        else:
            fee_pct = EST_FEE_PCT_PER_TRADE
        return round(_safe_float(notional) * fee_pct / 100.0, 2)

    # ── Open risk budget ─────────────────────────────────────────────

    def register_open_risk(self, symbol, risk_dollars):
        self._maybe_reset()
        risk_dollars = round(_safe_float(risk_dollars), 2)
        slots = self._state.get("open_risk_slots", {})
        slots[symbol] = risk_dollars
        self._state["open_risk_slots"] = slots
        self._state["total_open_risk"] = round(sum(slots.values()), 2)
        self._persist()
        logger.info(
            "ConservativeMode: registered open risk $%.2f for %s "
            "(total: $%.2f / $%.2f)",
            risk_dollars, symbol,
            self._state["total_open_risk"],
            self._state.get("max_open_risk_usd", 0),
        )

    def release_open_risk(self, symbol):
        self._maybe_reset()
        slots = self._state.get("open_risk_slots", {})
        released = slots.pop(symbol, 0.0)
        self._state["open_risk_slots"] = slots
        self._state["total_open_risk"] = round(sum(slots.values()), 2)
        self._persist()
        if released:
            logger.info(
                "ConservativeMode: released $%.2f risk for %s (total: $%.2f)",
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

        # Per-asset pause
        if self._state["asset_streaks"].get(symbol, 0) >= ASSET_CONSECUTIVE_LOSS_LIMIT:
            if symbol not in self._state["paused_assets"]:
                self._state["paused_assets"].append(symbol)
                logger.warning(
                    "ConservativeMode: %s paused after %d consecutive losses",
                    symbol, ASSET_CONSECUTIVE_LOSS_LIMIT,
                )

        # Per-strategy disable
        if self._state["strategy_streaks"].get(strategy, 0) >= STRATEGY_CONSECUTIVE_LOSS_LIMIT:
            if strategy not in self._state["disabled_strategies"]:
                self._state["disabled_strategies"].append(strategy)
                logger.warning(
                    "ConservativeMode: strategy '%s' disabled after %d consecutive losses",
                    strategy, STRATEGY_CONSECUTIVE_LOSS_LIMIT,
                )

        # Log
        self._state["trade_log"].append({
            "symbol": symbol,
            "strategy": strategy,
            "net_pl": round(net_pl, 2),
            "cumulative_pl": self._state["realized_pl"],
            "timestamp": _utcnow().isoformat(),
            "reason": reason[:120],
        })
        self._state["trade_log"] = self._state["trade_log"][-50:]

        self._recompute_daily_mode()
        self._persist()

    # ── Update unrealized P&L ────────────────────────────────────────

    def update_unrealized(self, unrealized_pl):
        """Call each cycle with total unrealized P&L from open positions."""
        self._maybe_reset()
        self._state["unrealized_pl"] = round(_safe_float(unrealized_pl), 2)
        self._recompute_daily_mode()
        self._persist()

    # ── Daily mode logic ─────────────────────────────────────────────

    def _compute_net_daily_pl(self):
        """Net daily P&L = realized + unrealized - estimated fees."""
        gross = (
            _safe_float(self._state.get("realized_pl"))
            + _safe_float(self._state.get("unrealized_pl"))
        )
        fees = _safe_float(self._state.get("estimated_fees"))
        return round(gross - fees, 2)

    def _recompute_daily_mode(self):
        """Determine which daily mode is active based on net P&L thresholds."""
        net_pl = self._compute_net_daily_pl()
        self._state["net_daily_pl"] = net_pl

        profit_threshold = _safe_float(self._state.get("profit_threshold_usd"))
        loss_threshold = _safe_float(self._state.get("loss_threshold_usd"))

        if profit_threshold > 0 and net_pl >= profit_threshold:
            new_mode = MODE_PROFIT_PROTECTION
            reason = (
                f"Daily net P&L ${net_pl:+.2f} reached profit threshold "
                f"${profit_threshold:.2f} ({DAILY_PROFIT_THRESHOLD_PCT:.2f}% of equity). "
                f"Only 90+ score trades allowed."
            )
        elif loss_threshold < 0 and net_pl <= loss_threshold:
            new_mode = MODE_LOSS_RECOVERY
            reason = (
                f"Daily net P&L ${net_pl:+.2f} hit loss threshold "
                f"${loss_threshold:.2f} ({DAILY_LOSS_THRESHOLD_PCT:.2f}% of equity). "
                f"Only 90+ score trades allowed. No revenge trading."
            )
        else:
            new_mode = MODE_SAFE_TEST
            reason = (
                f"Daily net P&L ${net_pl:+.2f}. Normal {QUALITY_SCORE_NORMAL}+ "
                f"score trades allowed."
            )

        old_mode = self._state.get("daily_mode")
        self._state["daily_mode"] = new_mode
        self._state["daily_mode_reason"] = reason

        # Alert on mode transitions
        if old_mode and old_mode != new_mode:
            logger.warning("ConservativeMode: mode transition %s → %s", old_mode, new_mode)
            self._send_alert(reason)

    def get_daily_mode(self):
        """Return the current daily mode name."""
        self._maybe_reset()
        return self._state.get("daily_mode", MODE_SAFE_TEST)

    def get_required_quality_score(self, ceo_posture=None):
        """Return the minimum trade quality score for the current mode.

        Adaptive thresholds:
          - Protection modes (profit/loss threshold hit): 90
          - Normal + aggressive CEO posture (strong risk-on): 72
          - Normal + defensive CEO posture (choppy market): 85
          - Normal + normal CEO posture: 75
        """
        mode = self.get_daily_mode()
        if mode in (MODE_PROFIT_PROTECTION, MODE_LOSS_RECOVERY):
            return QUALITY_SCORE_PROTECTION
        # Adaptive based on CEO posture
        if ceo_posture == "aggressive":
            return QUALITY_SCORE_RISK_ON
        if ceo_posture == "defensive":
            return QUALITY_SCORE_CHOPPY
        return QUALITY_SCORE_NORMAL

    def get_min_risk_reward(self):
        """Return the minimum R:R for the current mode."""
        mode = self.get_daily_mode()
        if mode in (MODE_PROFIT_PROTECTION, MODE_LOSS_RECOVERY):
            return MIN_RISK_REWARD_PROTECTION
        return MIN_RISK_REWARD_NORMAL

    # ── Can trade? ───────────────────────────────────────────────────

    def can_trade(self, symbol=None, strategy=None, risk_reward=0.0,
                  proposed_risk_dollars=0.0, open_position_count=0):
        """
        Return (allowed: bool, reason: str).
        Must pass ALL gates to trade.  No fixed max-trades-per-day gate.
        """
        self._maybe_reset()

        mode = self.get_daily_mode()
        min_rr = self.get_min_risk_reward()

        # Gate 1: Max open positions
        if open_position_count >= MAX_OPEN_POSITIONS:
            return False, (
                f"Max open positions reached: {open_position_count} >= {MAX_OPEN_POSITIONS}"
            )

        # Gate 2: Asset paused (consecutive loss cooldown)
        if symbol and symbol in self._state.get("paused_assets", []):
            streak = self._state.get("asset_streaks", {}).get(symbol, 0)
            return False, (
                f"{symbol} paused for today after {streak} consecutive losses"
            )

        # Gate 3: Strategy disabled (consecutive loss disable)
        if strategy and strategy in self._state.get("disabled_strategies", []):
            streak = self._state.get("strategy_streaks", {}).get(strategy, 0)
            return False, (
                f"Strategy '{strategy}' disabled after {streak} consecutive losses"
            )

        # Gate 4: Minimum R:R (dynamic — 1.5 normal, 2.0 in protection)
        if risk_reward > 0 and risk_reward < min_rr:
            return False, (
                f"Risk:reward {risk_reward:.2f} below minimum {min_rr:.2f} "
                f"(mode: {mode})"
            )

        # Gate 5: Open risk budget
        if proposed_risk_dollars > 0:
            max_open_risk = self._state.get("max_open_risk_usd", 0)
            current_open_risk = self._state.get("total_open_risk", 0)
            if max_open_risk > 0:
                new_total = current_open_risk + proposed_risk_dollars
                if new_total > max_open_risk:
                    return False, (
                        f"Open risk budget exceeded: "
                        f"current ${current_open_risk:.2f} + proposed "
                        f"${proposed_risk_dollars:.2f} = ${new_total:.2f} "
                        f"> budget ${max_open_risk:.2f} "
                        f"({MAX_OPEN_RISK_BUDGET_PCT:.2f}% of equity)"
                    )

        # Gate 6: Per-trade risk cap
        if proposed_risk_dollars > 0:
            max_per_trade = self._state.get("risk_per_trade_usd", 0)
            if max_per_trade > 0 and proposed_risk_dollars > max_per_trade * 1.05:
                return False, (
                    f"Per-trade risk ${proposed_risk_dollars:.2f} exceeds maximum "
                    f"${max_per_trade:.2f} ({RISK_PER_TRADE_PCT:.2f}% of equity)"
                )

        return True, f"Trade allowed (mode: {mode})"

    def get_confidence_boost(self):
        """Return extra confidence required in protection modes."""
        self._maybe_reset()
        mode = self.get_daily_mode()
        if mode in (MODE_PROFIT_PROTECTION, MODE_LOSS_RECOVERY):
            return GREEN_CONFIDENCE_BOOST
        return 0.0

    # ── Status for dashboard ─────────────────────────────────────────

    def get_status(self):
        """Return full state for dashboard display."""
        self._maybe_reset()
        mode = self.get_daily_mode()
        net_pl = self._compute_net_daily_pl()
        gross_pl = round(
            _safe_float(self._state.get("realized_pl"))
            + _safe_float(self._state.get("unrealized_pl")), 2
        )
        fees = _safe_float(self._state.get("estimated_fees"))

        profit_threshold = _safe_float(self._state.get("profit_threshold_usd"))
        loss_threshold = _safe_float(self._state.get("loss_threshold_usd"))
        profit_reached = profit_threshold > 0 and net_pl >= profit_threshold
        loss_reached = loss_threshold < 0 and net_pl <= loss_threshold

        # Dashboard message
        if profit_reached:
            dashboard_message = (
                f"Daily net P&L is ${net_pl:+.2f}. Profit threshold reached. "
                f"System is now in {MODE_PROFIT_PROTECTION}. "
                f"Only {QUALITY_SCORE_PROTECTION}+ score trades allowed."
            )
        elif loss_reached:
            dashboard_message = (
                f"Daily net P&L is ${net_pl:+.2f}. Loss threshold reached. "
                f"System is now in {MODE_LOSS_RECOVERY}. "
                f"Only {QUALITY_SCORE_PROTECTION}+ score trades allowed."
            )
        else:
            dashboard_message = (
                f"Daily net P&L is ${net_pl:+.2f}. {MODE_SAFE_TEST} active. "
                f"Normal {QUALITY_SCORE_NORMAL}+ score trades allowed."
            )

        return {
            # Current daily mode
            "daily_mode": mode,
            "daily_mode_reason": self._state.get("daily_mode_reason", ""),
            "dashboard_message": dashboard_message,
            "required_quality_score": self.get_required_quality_score(),
            "min_risk_reward": self.get_min_risk_reward(),
            # Account
            "date": self._state.get("date"),
            "equity": self._state.get("equity", 0),
            # P&L breakdown
            "realized_pl": self._state.get("realized_pl", 0),
            "unrealized_pl": self._state.get("unrealized_pl", 0),
            "gross_daily_pl": gross_pl,
            "estimated_fees": fees,
            "net_daily_pl": net_pl,
            # Thresholds
            "profit_threshold_usd": profit_threshold,
            "profit_threshold_pct": DAILY_PROFIT_THRESHOLD_PCT,
            "profit_threshold_reached": profit_reached,
            "loss_threshold_usd": loss_threshold,
            "loss_threshold_pct": DAILY_LOSS_THRESHOLD_PCT,
            "loss_threshold_reached": loss_reached,
            # Risk config
            "risk_per_trade_usd": self._state.get("risk_per_trade_usd", 0),
            "risk_per_trade_pct": RISK_PER_TRADE_PCT,
            "max_open_risk_pct": MAX_OPEN_RISK_BUDGET_PCT,
            "est_fee_pct_per_trade": EST_FEE_PCT_PER_TRADE,
            # Trade stats
            "trades_today": self._state.get("trades_today", 0),
            "wins_today": self._state.get("wins_today", 0),
            "losses_today": self._state.get("losses_today", 0),
            "win_rate_today": round(
                self._state.get("wins_today", 0)
                / max(1, self._state.get("trades_today", 1)) * 100, 1
            ),
            # Open risk
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
            # Trade log
            "trade_log": self._state.get("trade_log", [])[-10:],
        }

    # ── Alerts ───────────────────────────────────────────────────────

    def _send_alert(self, message):
        try:
            from telegram_notifier import send_alert, is_configured
            if is_configured():
                mode = self.get_daily_mode()
                send_alert(
                    f"🛡️ [{mode}]\n\n{message}",
                    level="warning",
                )
        except Exception:
            pass

    # ── Manual controls ──────────────────────────────────────────────

    def reset_strategy(self, strategy):
        self._maybe_reset()
        if strategy in self._state.get("disabled_strategies", []):
            self._state["disabled_strategies"].remove(strategy)
        self._state["strategy_streaks"][strategy] = 0
        self._persist()

    def reset_asset(self, symbol):
        self._maybe_reset()
        if symbol in self._state.get("paused_assets", []):
            self._state["paused_assets"].remove(symbol)
        self._state["asset_streaks"][symbol] = 0
        self._persist()

    def force_lock(self, reason="Manual lock"):
        """Not used as a primary mechanism any more; kept for emergency."""
        self._maybe_reset()
        # We no longer hard-lock.  Modes handle it.
        logger.warning("force_lock called: %s", reason)

    def force_unlock(self):
        """Not used as a primary mechanism any more; kept for emergency."""
        self._maybe_reset()
        logger.warning("force_unlock called")
