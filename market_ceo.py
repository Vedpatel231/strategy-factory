"""CEO layer: market intelligence and desk posture."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

import config
from intraday_engine import FeatureSet, MarketDataProvider


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _mean(values):
    values = [_safe_float(v) for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0


@dataclass
class MarketInstruction:
    posture: str
    risk_multiplier: float
    allow_new_longs: bool
    preferred_strategies: List[str]
    avoid_strategies: List[str]
    notes: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class CEOState:
    timestamp: str
    market_direction: str
    market_regime: str
    volatility_condition: str
    trend_strength: float
    crypto_market_condition: str
    stock_market_condition: str
    posture: str
    risk_multiplier: float
    confidence: float
    instructions: Dict[str, Dict]
    reasons: List[str]
    assets_analyzed: Dict[str, Dict]
    # Separate regimes for stock and crypto — set after construction
    stock_market_regime: str = "unknown"
    crypto_market_regime: str = "unknown"

    def to_dict(self):
        return asdict(self)


class MarketCEO:
    """Analyze broad market conditions every cycle.

    The CEO does not place orders.  It emits posture and strategy preference
    instructions that asset managers use when ranking bots.
    """

    def __init__(self, data_provider=None):
        self.data = data_provider or MarketDataProvider()

    def analyze(self):
        crypto_symbols = [f"{asset}/USD" for asset in config.CRYPTO_ASSETS[:4]]
        # Scan a representative sample of stocks (top 20) for CEO market read.
        # We don't need all 100 for regime detection — top 20 captures the market.
        stock_symbols = list(config.STOCK_ASSETS[:20])
        assets = {}

        for symbol in crypto_symbols + stock_symbols:
            assets[symbol] = self._analyze_symbol(symbol)

        crypto_rows = [v for k, v in assets.items() if "/" in k and v.get("data_ok")]
        stock_rows = [v for k, v in assets.items() if "/" not in k and v.get("data_ok")]

        crypto_condition = self._bucket_group(crypto_rows)
        stock_condition = self._bucket_group(stock_rows)
        all_rows = crypto_rows + stock_rows

        bullish_votes = sum(1 for row in all_rows if row.get("direction") == "bullish")
        bearish_votes = sum(1 for row in all_rows if row.get("direction") == "bearish")
        total_votes = max(1, len(all_rows))
        avg_adx = _mean(row.get("adx", 0) for row in all_rows)
        avg_atr = _mean(row.get("atr_pct", 0) for row in all_rows)
        avg_volume = _mean(row.get("volume_ratio", 1) for row in all_rows) or 1.0

        if bullish_votes / total_votes >= 0.55:
            direction = "bullish"
        elif bearish_votes / total_votes >= 0.55:
            direction = "bearish"
        else:
            direction = "sideways"

        if not all_rows:
            regime = "unknown"
            volatility = "unknown"
            posture = "defensive"
            confidence = 0.1
            reasons = ["No broad-market candles loaded; managers will run in defensive diagnostic mode."]
        else:
            if avg_atr >= 5.0:
                volatility = "volatile"
            elif avg_atr <= 1.0:
                volatility = "low_volatility"
            else:
                volatility = "normal_volatility"

            if direction == "bearish" and avg_adx >= 20:
                regime = "risk_off"
            elif avg_adx >= 25 and direction == "bullish":
                regime = "trending"
            elif avg_adx <= 16 and avg_volume < 0.9:
                regime = "low_volume"
            elif avg_adx <= 18:
                regime = "ranging"
            elif avg_volume >= 1.25 and avg_atr >= 1.2:
                regime = "breakout_ready"
            else:
                regime = "sideways"

            if regime == "risk_off" or (direction == "bearish" and avg_atr >= 3.0):
                posture = "defensive"
            elif volatility == "volatile" and direction != "bullish":
                posture = "defensive"
            elif direction == "bullish" and regime in ("trending", "breakout_ready"):
                posture = "aggressive"
            elif regime in ("unknown", "low_volume"):
                posture = "defensive"
            else:
                posture = "normal"

            confidence = min(0.95, 0.35 + (abs(bullish_votes - bearish_votes) / total_votes * 0.35) + min(avg_adx / 100, 0.25))
            reasons = [
                f"{bullish_votes}/{len(all_rows)} analyzed assets bullish, {bearish_votes}/{len(all_rows)} bearish.",
                f"Average ADX {avg_adx:.1f}, ATR% {avg_atr:.2f}, volume {avg_volume:.2f}x.",
                f"Crypto condition {crypto_condition}; stock condition {stock_condition}.",
            ]

        risk_multiplier = {
            "aggressive": 1.15,
            "normal": 0.85,
            "defensive": 0.45,
            "paused": 0.0,
        }.get(posture, 0.65)

        instructions = self._build_instructions(regime, direction, posture, risk_multiplier)

        # Compute separate stock and crypto regimes for managers
        stock_regime = self._compute_group_regime(stock_rows) if stock_rows else regime
        crypto_regime = self._compute_group_regime(crypto_rows) if crypto_rows else regime

        state = CEOState(
            timestamp=_utcnow(),
            market_direction=direction,
            market_regime=regime,
            volatility_condition=volatility,
            trend_strength=round(avg_adx, 2),
            crypto_market_condition=crypto_condition,
            stock_market_condition=stock_condition,
            posture=posture,
            risk_multiplier=risk_multiplier,
            confidence=round(confidence, 3),
            instructions={k: v.to_dict() for k, v in instructions.items()},
            reasons=reasons,
            assets_analyzed=assets,
        )
        # Attach separate regimes for managers (accessible via to_dict)
        state.stock_market_regime = stock_regime
        state.crypto_market_regime = crypto_regime
        return state

    def _analyze_symbol(self, symbol):
        # Use the primary entry timeframe for CEO analysis (30m by default)
        entry_tf = getattr(config, "DESK_ENTRY_TIMEFRAME", "30m")
        try:
            candles = self.data.get_candles(symbol, entry_tf, limit=200)
        except Exception as exc:
            return {"symbol": symbol, "data_ok": False, "reason": f"candle fetch failed: {exc}"}
        if len(candles) < 40:
            return {
                "symbol": symbol,
                "data_ok": False,
                "reason": f"only {len(candles)} {entry_tf} candles loaded",
            }
        features = FeatureSet(candles)
        close = features.close
        ema20 = features.ema20[-1] if features.ema20 else close
        ema50 = features.ema50[-1] if features.ema50 else close
        adx = features.adx_14[-1] if features.adx_14 else 0.0
        plus_di = features.plus_di_14[-1] if features.plus_di_14 else 0.0
        minus_di = features.minus_di_14[-1] if features.minus_di_14 else 0.0

        # RELAXED direction logic — score-based instead of requiring ALL conditions.
        # Each bullish/bearish signal adds points. Majority wins.
        bull_score = 0
        bear_score = 0
        if close > ema20:
            bull_score += 1
        elif close < ema20:
            bear_score += 1
        if ema20 > ema50:
            bull_score += 1
        elif ema20 < ema50:
            bear_score += 1
        if plus_di > minus_di:
            bull_score += 1
        elif minus_di > plus_di:
            bear_score += 1

        if bull_score >= 2:
            direction = "bullish"
        elif bear_score >= 2:
            direction = "bearish"
        else:
            direction = "sideways"

        if adx >= 25:
            regime = "trending"
        elif adx <= 16:
            regime = "ranging"
        elif features.volume_ratio >= 1.25:
            regime = "breakout_ready"
        else:
            regime = "sideways"
        return {
            "symbol": symbol,
            "data_ok": True,
            "direction": direction,
            "regime": regime,
            "adx": round(adx, 2),
            "atr_pct": round(features.atr_pct, 3),
            "volume_ratio": round(features.volume_ratio, 3),
            "close": round(close, 6),
            "bull_score": bull_score,
            "bear_score": bear_score,
            "reason": f"{direction} {regime}: close={close:.2f}, EMA20={ema20:.2f}, EMA50={ema50:.2f}, ADX={adx:.1f}, bull={bull_score}/bear={bear_score}",
        }

    def _bucket_group(self, rows):
        if not rows:
            return "unknown"
        bullish = sum(1 for row in rows if row.get("direction") == "bullish")
        bearish = sum(1 for row in rows if row.get("direction") == "bearish")
        avg_adx = _mean(row.get("adx", 0) for row in rows)
        avg_atr = _mean(row.get("atr_pct", 0) for row in rows)
        if bearish > bullish and avg_adx >= 20:
            return "risk_off"
        if bullish > bearish and avg_adx >= 22:
            return "risk_on"
        if avg_atr >= 5:
            return "volatile"
        if avg_adx <= 16:
            return "range_bound"
        return "mixed"

    def _compute_group_regime(self, rows):
        """Compute regime for a group of assets (stocks or crypto separately)."""
        if not rows:
            return "unknown"
        bullish = sum(1 for r in rows if r.get("direction") == "bullish")
        bearish = sum(1 for r in rows if r.get("direction") == "bearish")
        total = max(1, len(rows))
        avg_adx = _mean(r.get("adx", 0) for r in rows)
        avg_atr = _mean(r.get("atr_pct", 0) for r in rows)
        avg_vol = _mean(r.get("volume_ratio", 1) for r in rows) or 1.0

        if bearish / total >= 0.55 and avg_adx >= 20:
            return "risk_off"
        if bullish / total >= 0.55 and avg_adx >= 22:
            return "trending"
        if avg_adx <= 16 and avg_vol < 0.9:
            return "low_volume"
        if avg_adx <= 18:
            return "ranging"
        if avg_vol >= 1.25 and avg_atr >= 1.0:
            return "breakout_ready"
        return "sideways"

    def _build_instructions(self, regime, direction, posture, risk_multiplier):
        trend = ["trend_pullback", "ema_crossover", "macd_momentum", "breakout_retest",
                 "donchian_breakout", "atr_momentum_expansion", "supertrend_continuation"]
        mean = ["rsi_mean_reversion", "bollinger_reversion", "vwap_bounce"]
        if regime in ("trending", "breakout_ready"):
            preferred = trend
            avoid = [] if direction == "bullish" else mean
        elif regime in ("ranging", "sideways", "low_volume"):
            preferred = mean
            avoid = ["donchian_breakout", "atr_momentum_expansion"]
        elif regime == "risk_off":
            preferred = ["vwap_bounce", "rsi_mean_reversion"]
            avoid = ["donchian_breakout", "atr_momentum_expansion", "ema_crossover"]
        else:
            preferred = ["trend_pullback", "vwap_bounce", "macd_momentum"]
            avoid = []

        allow_new_longs = posture != "paused" and not (regime == "risk_off" and direction == "bearish")
        notes = [f"CEO posture {posture}; regime {regime}; direction {direction}."]
        if not allow_new_longs:
            notes.append("New long entries should be rare or blocked until risk improves.")
        instruction = MarketInstruction(
            posture=posture,
            risk_multiplier=risk_multiplier,
            allow_new_longs=allow_new_longs,
            preferred_strategies=preferred,
            avoid_strategies=avoid,
            notes=notes,
        )
        return {"crypto": instruction, "stock": instruction}
