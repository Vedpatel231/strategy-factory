"""Asset manager layer: ranks strategy bots for one symbol.

Key rules:
  - CLOSED CANDLE RULE: Only act on fully closed 1H candles.  The last
    candle in the array may be incomplete (still forming), so we drop it.
  - DUPLICATE SIGNAL PREVENTION: Track the last signal per symbol to avoid
    entering the same setup on consecutive cycles within the same candle.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import config
from bot_registry import StrategyBot
from decision_logger import DecisionLogger
from strategies import build_feature_context
from trade_journal import load_trade_journal

# File to track last entry signal per symbol to prevent same-candle duplicates
_LAST_SIGNAL_FILE = os.path.join(config.DATA_DIR, "last_entry_signals.json")


def _utcnow():
    return datetime.now(timezone.utc)


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ManagerDecision:
    symbol: str
    asset_class: str
    timestamp: str
    ceo_posture: str
    ceo_regime: str
    action: str
    active_bot: Optional[str]
    active_strategy: Optional[str]
    confidence: float
    score: float
    reason: str
    rejection_reason: str
    selected_signal: Optional[Dict]
    closest_bot: Optional[Dict]
    bots_on_hold: List[Dict] = field(default_factory=list)
    trade_request: Optional[Dict] = None
    cooldown_remaining_minutes: int = 0
    open_position: bool = False

    def to_dict(self):
        return asdict(self)


# Hard regime compatibility: strategies whose works_best has ZERO overlap
# with the compatible set for the current CEO regime are blocked from entry.
# This prevents e.g. breakout strategies entering during ranging markets.
REGIME_COMPATIBLE_TAGS = {
    "trending":       {"trending", "risk_on", "bullish", "breakout_ready"},
    "ranging":        {"ranging", "sideways", "mean_reversion", "low_volatility"},
    "sideways":       {"sideways", "ranging", "mean_reversion", "low_volatility", "bullish"},
    "breakout_ready": {"breakout_ready", "trending", "volatile", "risk_on"},
    "risk_off":       {"ranging", "sideways", "mean_reversion", "low_volatility"},
    "low_volume":     {"ranging", "sideways", "low_volatility", "mean_reversion"},
}


def _regime_compatible(regime, works_best):
    """Return True if the strategy is allowed to trade in the current regime."""
    compatible = REGIME_COMPATIBLE_TAGS.get(regime)
    if compatible is None:
        return True  # unknown regime → allow everything
    return bool(set(works_best or []) & compatible)


def _drop_incomplete_candle(candles):
    """Drop the last candle if it might be incomplete (still forming).

    Most data providers include the current forming candle at the end.
    By removing it we ensure strategies only see fully closed candles,
    preventing entries based on partial data that could reverse.
    """
    if candles and len(candles) > 1:
        return candles[:-1]
    return candles


def _read_last_signals():
    try:
        with open(_LAST_SIGNAL_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_last_signals(data):
    try:
        os.makedirs(os.path.dirname(_LAST_SIGNAL_FILE), exist_ok=True)
        with open(_LAST_SIGNAL_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception:
        pass


def _candle_key(candles):
    """Return a unique key for the last closed candle (timestamp + close)."""
    if not candles:
        return ""
    last = candles[-1]
    ts = last.get("timestamp") or last.get("time") or last.get("t") or ""
    close = last.get("close") or last.get("c") or ""
    return f"{ts}:{close}"


def _is_duplicate_signal(symbol, strategy, candle_key):
    """Check if we already generated an entry signal for this candle."""
    signals = _read_last_signals()
    key = f"{symbol}:{strategy}"
    last = signals.get(key)
    if last and last.get("candle_key") == candle_key:
        return True
    return False


def _record_signal(symbol, strategy, candle_key):
    """Record that we produced an entry signal for this candle."""
    signals = _read_last_signals()
    key = f"{symbol}:{strategy}"
    signals[key] = {
        "candle_key": candle_key,
        "timestamp": _utcnow().isoformat(),
    }
    # Prune old entries (keep last 100)
    if len(signals) > 100:
        sorted_keys = sorted(signals.keys(), key=lambda k: signals[k].get("timestamp", ""))
        for old_key in sorted_keys[:-100]:
            signals.pop(old_key, None)
    _write_last_signals(signals)


# ── Trade Quality Score ──────────────────────────────────────────────
#
# Composite 0-100 score that determines if a setup is worth taking.
# Thresholds are now DYNAMIC — set by conservative_mode based on daily
# net P&L:
#   SAFE_TEST_MODE               → 75
#   PROFIT_PROTECTION_MODE       → 90
#   LOSS_RECOVERY_PROTECTION_MODE → 90
# Components:
#   1. Regime alignment (0-20): strategy works_best matches CEO regime
#   2. HTF confirmation (0-20): 4H and 1D trend agree with entry direction
#   3. Risk:reward (0-20): scales from 1.5 (minimum) to 3.0+ (excellent)
#   4. Volume confirmation (0-15): above-average volume on signal candle
#   5. Extension from mean (0-10): not over-extended from key EMAs
#   6. Strategy performance (0-15): learning engine historical score


def compute_trade_quality(signal_row, features, ceo_state, regime, learner=None, symbol=None):
    """Compute a 0-100 trade quality score for a buy candidate.

    Returns (score: int, breakdown: dict).
    """
    breakdown = {}
    total = 0.0

    # 1. Regime alignment (0-20)
    works_best = signal_row.get("works_best") or []
    compatible = REGIME_COMPATIBLE_TAGS.get(regime, set())
    overlap = len(set(works_best) & compatible) if compatible else 0
    if regime in works_best:
        regime_score = 20.0
    elif overlap >= 2:
        regime_score = 15.0
    elif overlap >= 1:
        regime_score = 10.0
    else:
        regime_score = 0.0
    breakdown["regime_alignment"] = round(regime_score, 1)
    total += regime_score

    # 2. HTF confirmation (0-20)
    # FIXED: "sideways" no longer gives 0 — it gets a moderate score.
    # This prevents the bottleneck where sideways direction made it
    # impossible to reach quality threshold 75.
    htf_score = 0.0
    trend_bias = getattr(features, "trend_bias", None) or ""
    ceo_direction = ceo_state.market_direction if ceo_state else ""
    # 4H trend alignment
    if trend_bias == "bullish":
        htf_score += 10.0
    elif trend_bias in ("neutral", "sideways", ""):
        htf_score += 6.0  # was 5.0 / 0.0 — now gives fair credit
    # CEO direction alignment
    if ceo_direction == "bullish":
        htf_score += 10.0
    elif ceo_direction in ("sideways", "neutral", ""):
        htf_score += 6.0  # was 5.0 / 0.0 — sideways is NOT bearish
    elif ceo_direction == "bearish":
        htf_score -= 3.0  # less harsh penalty
    htf_score = max(0.0, min(20.0, htf_score))
    breakdown["htf_confirmation"] = round(htf_score, 1)
    total += htf_score

    # 3. Risk:reward (0-20)
    rr = _safe_float(signal_row.get("risk_reward"), 0.0)
    if rr >= 3.0:
        rr_score = 20.0
    elif rr >= 2.5:
        rr_score = 17.0
    elif rr >= 2.0:
        rr_score = 14.0
    elif rr >= 1.5:
        rr_score = 10.0
    else:
        rr_score = 0.0
    breakdown["risk_reward"] = round(rr_score, 1)
    total += rr_score

    # 4. Volume confirmation (0-15)
    vol_ratio = _safe_float(getattr(features, "volume_ratio", None), 1.0)
    if vol_ratio >= 1.8:
        vol_score = 15.0
    elif vol_ratio >= 1.4:
        vol_score = 12.0
    elif vol_ratio >= 1.1:
        vol_score = 8.0
    elif vol_ratio >= 0.8:
        vol_score = 4.0
    else:
        vol_score = 0.0
    breakdown["volume"] = round(vol_score, 1)
    total += vol_score

    # 5. Extension from mean (0-10) — penalize over-extension
    atr_pct = _safe_float(getattr(features, "atr_pct", None), 1.0)
    extension_pct = _safe_float(getattr(features, "extension_from_ema20_pct", None), 0.0)
    # If price is more than 2x ATR from EMA20, it's over-extended
    if abs(extension_pct) < atr_pct * 0.5:
        ext_score = 10.0  # Close to mean — ideal pullback entry
    elif abs(extension_pct) < atr_pct:
        ext_score = 7.0
    elif abs(extension_pct) < atr_pct * 1.5:
        ext_score = 4.0
    else:
        ext_score = 0.0  # Over-extended — risky entry
    breakdown["extension"] = round(ext_score, 1)
    total += ext_score

    # 6. Strategy performance / learning engine (0-15)
    perf_score = 7.5  # Default neutral
    if learner:
        try:
            quality = learner.get_strategy_quality(
                signal_row.get("strategy", ""),
                regime,
                symbol or "",
                signal_row.get("timeframe", "1h"),
            )
            adj = _safe_float(quality.get("score_adjustment"), 0.0)
            # Map learning adjustment (-20 to +20) into 0-15 range
            perf_score = max(0.0, min(15.0, 7.5 + adj * 0.5))
        except Exception:
            pass
    breakdown["strategy_performance"] = round(perf_score, 1)
    total += perf_score

    return int(round(total)), breakdown


class AssetManager:
    def __init__(self, symbol, asset_class, bots: List[StrategyBot], data_provider, learner=None, logger=None):
        self.symbol = symbol
        self.asset_class = asset_class
        self.bots = bots
        self.data = data_provider
        self.learner = learner
        self.logger = logger or DecisionLogger()

    def evaluate(self, ceo_state, open_position=None):
        now = _utcnow()
        cooldown_minutes, cooldown_reason = self._cooldown_remaining(now)
        open_position = open_position or None

        # CLOSED CANDLE RULE: drop the last (potentially incomplete) candle
        # from each timeframe so strategies only see fully closed data.
        # Multi-timeframe: load candles for the bot's own timeframe.
        # Default candles for features are loaded at 30m (primary entry tf).
        candles_30m = _drop_incomplete_candle(self._get_candles("30m", 300))
        candles_15m = _drop_incomplete_candle(self._get_candles("15m", 300))
        candles_1h = _drop_incomplete_candle(self._get_candles("1h", 221))
        candles_4h = _drop_incomplete_candle(self._get_candles("4h", 141))
        candles_1d = _drop_incomplete_candle(self._get_candles("1D", 121))

        # Use primary entry timeframe for features (30m default)
        primary_candles = candles_30m or candles_1h
        features = build_feature_context(primary_candles, {"4h": candles_4h, "1D": candles_1d})

        # Multi-timeframe candle map for bots at different timeframes
        self._tf_candles = {
            "15m": candles_15m,
            "30m": candles_30m,
            "1h": candles_1h,
            "4h": candles_4h,
            "1D": candles_1d,
        }

        # Track the candle key for duplicate signal prevention
        entry_candle_key = _candle_key(primary_candles)

        bot_rows = []
        for bot in self.bots:
            strategy = bot.create_strategy()
            # Multi-timeframe: use candles matching the bot's timeframe
            bot_candles = self._tf_candles.get(bot.timeframe, primary_candles)
            if bot_candles and len(bot_candles) >= 30:
                bot_features = build_feature_context(bot_candles, {"4h": candles_4h, "1D": candles_1d})
            else:
                bot_features = features  # fallback to primary
            try:
                signal = strategy.evaluate(bot_features)
            except Exception as exc:
                signal = strategy._hold(bot_features, 0.0, f"Strategy error: {exc}")
            score, score_parts = self._rank_signal(bot, signal, bot_features, ceo_state, cooldown_minutes, bool(open_position))
            signal_dict = signal.to_dict()
            signal_dict["features"] = bot_features.latest_features()
            signal_dict["score_parts"] = score_parts
            bot_rows.append({
                "bot_id": bot.bot_id,
                "bot_name": bot.display_name,
                "strategy": bot.strategy_name,
                "timeframe": bot.timeframe,
                "action": signal.action,
                "confidence": signal.confidence,
                "score": round(score, 2),
                "reason": signal.reason,
                "entry_reason": signal.entry_reason,
                "rejection_reason": signal.rejection_reason,
                "invalidation_reason": signal.invalidation_reason,
                "risk_reward": signal.risk_reward,
                "works_best": signal.works_best,
                "signal": signal_dict,
            })

        bot_rows.sort(key=lambda row: row["score"], reverse=True)
        selected = bot_rows[0] if bot_rows else None
        regime = ceo_state.market_regime
        buy_candidates = [
            row for row in bot_rows
            if row["action"] == "buy" and _regime_compatible(regime, row.get("works_best"))
        ]
        best_buy = buy_candidates[0] if buy_candidates else None
        closest = selected
        instruction = (ceo_state.instructions or {}).get(self.asset_class, {})
        allow_new_longs = bool(instruction.get("allow_new_longs", True))
        posture = ceo_state.posture
        threshold = self._confidence_threshold(posture)

        if open_position:
            reason = "Existing position is open; manager will manage lifecycle and will not duplicate exposure."
            action = "manage"
            trade_request = None
            active = selected
            rejection = reason
        elif cooldown_minutes > 0:
            reason = cooldown_reason
            action = "cooldown"
            trade_request = None
            active = selected
            rejection = reason
        elif not allow_new_longs:
            reason = "CEO is risk-off for new long entries."
            action = "wait"
            trade_request = None
            active = selected
            rejection = reason
        elif best_buy and best_buy["confidence"] >= threshold and best_buy["score"] >= 48:
            # TRADE QUALITY SCORE: composite 0-100 check
            quality_score, quality_breakdown = compute_trade_quality(
                best_buy, features, ceo_state, regime,
                learner=self.learner, symbol=self.symbol,
            )
            # Dynamic threshold from conservative_mode's daily P&L mode:
            #   SAFE_TEST_MODE + aggressive → 72,  normal → 75,
            #   defensive → 85,  protection modes → 90
            from conservative_mode import ConservativeMode
            _cm = ConservativeMode()
            quality_threshold = _cm.get_required_quality_score(ceo_posture=posture)
            current_daily_mode = _cm.get_daily_mode()

            if quality_score < quality_threshold:
                action = "wait"
                reason = (
                    f"Quality score {quality_score}/100 below threshold {quality_threshold} "
                    f"(mode: {current_daily_mode}) for {best_buy['bot_name']}. "
                    f"Breakdown: {quality_breakdown}"
                )
                trade_request = None
                active = best_buy
                rejection = reason
            # DUPLICATE SIGNAL PREVENTION: don't re-enter the same setup on
            # the same closed candle (prevents double entries within one hour).
            elif _is_duplicate_signal(self.symbol, best_buy["strategy"], entry_candle_key):
                action = "wait"
                reason = (
                    f"Duplicate signal blocked: {best_buy['bot_name']} already signalled "
                    f"on this candle for {self.symbol}."
                )
                trade_request = None
                active = best_buy
                rejection = reason
            else:
                active = best_buy
                signal = best_buy["signal"]
                trade_request = {
                    "symbol": self.symbol,
                    "asset_class": self.asset_class,
                    "side": "buy",
                    "bot_id": best_buy["bot_id"],
                    "bot_name": best_buy["bot_name"],
                    "strategy": best_buy["strategy"],
                    "timeframe": best_buy["timeframe"],
                    "confidence": best_buy["confidence"],
                    "manager_score": best_buy["score"],
                    "quality_score": quality_score,
                    "quality_breakdown": quality_breakdown,
                    "quality_threshold": quality_threshold,
                    "daily_mode": current_daily_mode,
                    "entry_price": features.close,
                    "stop_loss": signal.get("recommended_stop_loss"),
                    "take_profit": signal.get("recommended_take_profit"),
                    "partial_profit": signal.get("partial_profit_level"),
                    "trailing_stop": signal.get("trailing_stop_logic"),
                    "risk_reward": signal.get("risk_reward", 0),
                    "entry_reason": signal.get("entry_reason") or signal.get("reason"),
                    "invalidation_reason": signal.get("invalidation_reason"),
                    "ceo_regime": ceo_state.market_regime,
                    "ceo_posture": posture,
                    "ceo_risk_multiplier": ceo_state.risk_multiplier,
                    "market_direction": ceo_state.market_direction,
                    "features": features.latest_features(),
                }
                action = "enter"
                reason = f"Selected {best_buy['bot_name']} as best ranked buy candidate."
                rejection = ""
                # Record the signal to prevent duplicate entries on same candle
                _record_signal(self.symbol, best_buy["strategy"], entry_candle_key)
        else:
            active = selected
            trade_request = None
            action = "wait"
            # Check if regime filtering removed buy candidates
            all_buy_signals = [row for row in bot_rows if row["action"] == "buy"]
            regime_blocked = [r for r in all_buy_signals if not _regime_compatible(regime, r.get("works_best"))]
            if best_buy:
                reason = (
                    f"Closest buy is {best_buy['bot_name']} but confidence {best_buy['confidence']:.2f} "
                    f"is below {threshold:.2f} or score {best_buy['score']:.1f} is below 48."
                )
            elif regime_blocked and not buy_candidates:
                names = ", ".join(r["bot_name"] for r in regime_blocked[:3])
                reason = (
                    f"Buy signals from {names} blocked: regime '{regime}' is incompatible "
                    f"with their strategy type."
                )
            elif closest:
                reason = f"No buy signal. Closest bot is {closest['bot_name']}: {closest['reason']}"
            else:
                reason = "No bots available for this asset."
            rejection = reason

        decision = ManagerDecision(
            symbol=self.symbol,
            asset_class=self.asset_class,
            timestamp=now.isoformat(),
            ceo_posture=posture,
            ceo_regime=ceo_state.market_regime,
            action=action,
            active_bot=active.get("bot_name") if active else None,
            active_strategy=active.get("strategy") if active else None,
            confidence=round(_safe_float(active.get("confidence") if active else 0), 3),
            score=round(_safe_float(active.get("score") if active else 0), 2),
            reason=reason,
            rejection_reason=rejection,
            selected_signal=active.get("signal") if active else None,
            closest_bot=closest,
            bots_on_hold=[row for row in bot_rows if not active or row["bot_id"] != active.get("bot_id")],
            trade_request=trade_request,
            cooldown_remaining_minutes=cooldown_minutes,
            open_position=bool(open_position),
        )

        self.logger.append("manager_decision", {
            "symbol": self.symbol,
            "action": decision.action,
            "active_bot": decision.active_bot,
            "active_strategy": decision.active_strategy,
            "confidence": decision.confidence,
            "score": decision.score,
            "reason": decision.reason,
            "ceo_regime": ceo_state.market_regime,
            "ceo_posture": posture,
        })
        return decision

    def _get_candles(self, timeframe, limit):
        try:
            return self.data.get_candles(self.symbol, timeframe, limit=limit)
        except Exception:
            return []

    def _rank_signal(self, bot, signal, features, ceo_state, cooldown_minutes, has_open_position):
        instruction = (ceo_state.instructions or {}).get(bot.asset_class, {})
        preferred = set(instruction.get("preferred_strategies", []))
        avoid = set(instruction.get("avoid_strategies", []))
        regime = ceo_state.market_regime

        base = signal.confidence * 100.0
        action_bonus = 18.0 if signal.action == "buy" else 0.0
        regime_match = 0.0
        if bot.strategy_name in preferred:
            regime_match += 12.0
        if bot.strategy_name in avoid:
            regime_match -= 18.0
        if regime in signal.works_best:
            regime_match += 8.0
        if ceo_state.market_direction == "bullish" and features.trend_bias == "bullish":
            regime_match += 5.0
        if ceo_state.market_direction == "bearish" and signal.action == "buy":
            regime_match -= 12.0

        rr_bonus = min(10.0, max(0.0, signal.risk_reward - 1.0) * 4.0)
        volatility_penalty = 0.0
        if features.atr_pct > 8:
            volatility_penalty = -12.0
        elif features.atr_pct < 0.15:
            volatility_penalty = -8.0

        learning_adj = self._learning_adjustment(bot.strategy_name, regime, bot.timeframe)
        cooldown_penalty = -30.0 if cooldown_minutes > 0 else 0.0
        position_penalty = -25.0 if has_open_position else 0.0

        score = base + action_bonus + regime_match + rr_bonus + volatility_penalty + learning_adj + cooldown_penalty + position_penalty
        return score, {
            "base_confidence": round(base, 2),
            "action_bonus": action_bonus,
            "regime_match": round(regime_match, 2),
            "risk_reward_bonus": round(rr_bonus, 2),
            "volatility_penalty": volatility_penalty,
            "learning_adjustment": round(learning_adj, 2),
            "cooldown_penalty": cooldown_penalty,
            "position_penalty": position_penalty,
        }

    def _learning_adjustment(self, strategy_name, regime, timeframe):
        if not self.learner:
            return 0.0
        try:
            quality = self.learner.get_strategy_quality(strategy_name, regime, self.symbol, timeframe)
            return _safe_float(quality.get("score_adjustment"), 0.0)
        except Exception:
            return 0.0

    def _confidence_threshold(self, posture):
        if posture == "aggressive":
            return _safe_float(os.environ.get("DESK_AGGRESSIVE_CONFIDENCE", "0.46"))
        if posture == "defensive":
            return _safe_float(os.environ.get("DESK_DEFENSIVE_CONFIDENCE", "0.58"))
        if posture == "paused":
            return 1.0
        return _safe_float(os.environ.get("DESK_NORMAL_CONFIDENCE", "0.50"))

    def _cooldown_remaining(self, now):
        default_hours = _safe_float(os.environ.get("POST_TRADE_COOLDOWN_HOURS", "4.0"), 4.0)
        loss_hours = _safe_float(os.environ.get("POST_LOSS_COOLDOWN_HOURS", "8.0"), 8.0)
        try:
            events = load_trade_journal(limit=500)
        except Exception:
            events = []
        for event in events:
            if event.get("symbol") != self.symbol or event.get("event") != "position_closed":
                continue
            ts = event.get("timestamp") or event.get("closed_at")
            try:
                closed_at = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except Exception:
                continue
            pl_pct = _safe_float(event.get("unrealized_pl_pct"), 0.0)
            window = timedelta(hours=loss_hours if pl_pct < 0 else default_hours)
            remaining = (closed_at + window) - now
            if remaining.total_seconds() > 0:
                mins = int(remaining.total_seconds() // 60) + 1
                return mins, (
                    f"Post-trade cooldown active for {self.symbol}: {mins} minutes remaining "
                    f"after {'loss' if pl_pct < 0 else 'completed trade'}."
                )
            break
        return 0, ""
