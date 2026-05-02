"""Asset manager layer: ranks strategy bots for one symbol."""

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from bot_registry import StrategyBot
from decision_logger import DecisionLogger
from strategies import build_feature_context
from trade_journal import load_trade_journal


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
        candles_1h = self._get_candles("1h", 220)
        candles_4h = self._get_candles("4h", 140)
        candles_1d = self._get_candles("1D", 120)
        features = build_feature_context(candles_1h, {"4h": candles_4h, "1D": candles_1d})

        bot_rows = []
        for bot in self.bots:
            strategy = bot.create_strategy()
            try:
                signal = strategy.evaluate(features)
            except Exception as exc:
                signal = strategy._hold(features, 0.0, f"Strategy error: {exc}")
            score, score_parts = self._rank_signal(bot, signal, features, ceo_state, cooldown_minutes, bool(open_position))
            signal_dict = signal.to_dict()
            signal_dict["features"] = features.latest_features()
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
        buy_candidates = [row for row in bot_rows if row["action"] == "buy"]
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
        else:
            active = selected
            trade_request = None
            action = "wait"
            if best_buy:
                reason = (
                    f"Closest buy is {best_buy['bot_name']} but confidence {best_buy['confidence']:.2f} "
                    f"is below {threshold:.2f} or score {best_buy['score']:.1f} is below 48."
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
