"""Professional long-only strategy library.

Each strategy returns a full decision object.  A hold is still informative:
it includes the closest setup confidence, the blocker, and the condition the
bot is waiting for so the dashboard never looks idle or silent.
"""

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Type

from intraday_engine import (
    adx_system,
    atr,
    donchian_high,
    donchian_low,
    ema,
    macd,
    rsi,
    sma,
)
import config


ENTRY_TIMEFRAME = getattr(config, "DESK_ENTRY_TIMEFRAME", "1h")


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


def _clamp(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def _crossed_above(fast, slow, lookback=5):
    if len(fast) < lookback + 2 or len(slow) < lookback + 2:
        return False
    for idx in range(1, lookback + 1):
        if fast[-idx] > slow[-idx] and fast[-idx - 1] <= slow[-idx - 1]:
            return True
    return False


def _near(value, target, pct):
    if target <= 0:
        return False
    return abs(value - target) / target * 100.0 <= pct


def _bollinger(values, period=20, mult=2.0):
    mid = sma(values, period)
    upper = []
    lower = []
    for idx in range(len(values)):
        window = values[max(0, idx - period + 1):idx + 1]
        avg = mid[idx] if idx < len(mid) else _mean(window)
        if len(window) > 1:
            # Use sample std dev (N-1) to match TradingView/standard Bollinger Band calculation
            variance = sum((v - avg) ** 2 for v in window) / (len(window) - 1)
            std = variance ** 0.5
        else:
            std = 0.0
        upper.append(avg + (std * mult))
        lower.append(avg - (std * mult))
    return upper, mid, lower


def _rolling_vwap(candles, lookback=20):
    window = candles[-lookback:] if len(candles) >= lookback else candles
    volume_sum = sum(_safe_float(c.get("volume")) for c in window)
    if volume_sum <= 0:
        return _mean([
            (_safe_float(c.get("high")) + _safe_float(c.get("low")) + _safe_float(c.get("close"))) / 3
            for c in window
        ])
    return sum(
        ((_safe_float(c.get("high")) + _safe_float(c.get("low")) + _safe_float(c.get("close"))) / 3)
        * _safe_float(c.get("volume"))
        for c in window
    ) / volume_sum


def _supertrend(candles, period=10, multiplier=3.0):
    if not candles:
        return [], []
    atr_vals = atr(candles, period)
    basic_upper = []
    basic_lower = []
    for idx, candle in enumerate(candles):
        hl2 = (_safe_float(candle.get("high")) + _safe_float(candle.get("low"))) / 2
        a = atr_vals[idx] if idx < len(atr_vals) else 0.0
        basic_upper.append(hl2 + multiplier * a)
        basic_lower.append(hl2 - multiplier * a)

    final_upper = [basic_upper[0]]
    final_lower = [basic_lower[0]]
    trend = [1]
    for idx in range(1, len(candles)):
        prev_close = _safe_float(candles[idx - 1].get("close"))
        upper = basic_upper[idx]
        lower = basic_lower[idx]
        prev_upper = final_upper[-1]
        prev_lower = final_lower[-1]
        final_upper.append(upper if upper < prev_upper or prev_close > prev_upper else prev_upper)
        final_lower.append(lower if lower > prev_lower or prev_close < prev_lower else prev_lower)

        close = _safe_float(candles[idx].get("close"))
        if trend[-1] < 0 and close > final_upper[-1]:
            trend.append(1)
        elif trend[-1] > 0 and close < final_lower[-1]:
            trend.append(-1)
        else:
            trend.append(trend[-1])
    active_line = [
        final_lower[idx] if trend[idx] > 0 else final_upper[idx]
        for idx in range(len(candles))
    ]
    return active_line, trend


class FeatureContext:
    """Precomputed entry-timeframe features plus higher-timeframe confirmation."""

    def __init__(self, candles, higher_timeframes=None):
        self.candles = candles or []
        self.higher_timeframes = higher_timeframes or {}
        self.closes = [_safe_float(c.get("close")) for c in self.candles]
        self.highs = [_safe_float(c.get("high")) for c in self.candles]
        self.lows = [_safe_float(c.get("low")) for c in self.candles]
        self.opens = [_safe_float(c.get("open")) for c in self.candles]
        self.volumes = [_safe_float(c.get("volume")) for c in self.candles]
        self.close = self.closes[-1] if self.closes else 0.0
        self.open = self.opens[-1] if self.opens else self.close
        self.high = self.highs[-1] if self.highs else self.close
        self.low = self.lows[-1] if self.lows else self.close

        self.ema9 = ema(self.closes, 9)
        self.ema20 = ema(self.closes, 20)
        self.ema21 = ema(self.closes, 21)
        self.ema50 = ema(self.closes, 50)
        self.ema200 = ema(self.closes, 200)
        self.rsi14 = rsi(self.closes, 14)
        self.macd_line, self.macd_signal, self.macd_histogram = macd(self.closes)
        self.atr14 = atr(self.candles, 14)
        self.adx14, self.plus_di14, self.minus_di14 = adx_system(self.candles, 14)
        self.bb_upper, self.bb_mid, self.bb_lower = _bollinger(self.closes, 20, 2.0)
        self.donchian_high20 = donchian_high(self.highs, 20)
        self.donchian_low20 = donchian_low(self.lows, 20)
        self.vwap20 = _rolling_vwap(self.candles, 20)
        self.supertrend_line, self.supertrend_direction = _supertrend(self.candles)

    @property
    def enough(self):
        return len(self.candles) >= 60 and self.close > 0

    @property
    def atr_value(self):
        return self.atr14[-1] if self.atr14 else max(self.close * 0.015, 0.01)

    @property
    def atr_pct(self):
        return (self.atr_value / self.close * 100.0) if self.close else 0.0

    @property
    def rsi_value(self):
        return self.rsi14[-1] if self.rsi14 else 50.0

    @property
    def adx_value(self):
        return self.adx14[-1] if self.adx14 else 0.0

    @property
    def volume_ratio(self):
        # Use last 3 bars vs 20-bar baseline for more responsive volume detection.
        # If volume data is sparse (common for Alpaca crypto), return 1.0 (neutral)
        # to avoid false signals from noise.
        if len(self.volumes) < 10:
            return 1.0
        recent = _mean(self.volumes[-3:])
        base = _mean(self.volumes[-25:-3]) if len(self.volumes) >= 28 else _mean(self.volumes[:-3])
        if base <= 0 or recent <= 0:
            return 1.0  # No reliable volume data — neutral
        return recent / base

    @property
    def bullish_bar(self):
        return self.close > self.open and self.close >= self.low + ((self.high - self.low) * 0.55)

    @property
    def trend_bias(self):
        if not self.ema50 or not self.ema200:
            return "unknown"
        ema50 = self.ema50[-1]
        ema200 = self.ema200[-1] if len(self.ema200) >= 200 else self.ema50[-1]
        if self.close > ema50 and ema50 >= ema200:
            return "bullish"
        if self.close < ema50 and ema50 <= ema200:
            return "bearish"
        return "sideways"

    @property
    def htf_bullish(self):
        votes = 0
        total = 0
        for candles in self.higher_timeframes.values():
            closes = [_safe_float(c.get("close")) for c in (candles or [])]
            if len(closes) < 50:
                continue
            total += 1
            e20 = ema(closes, 20)[-1]
            e50 = ema(closes, 50)[-1]
            if closes[-1] >= e20 >= e50:
                votes += 1
        if total == 0:
            return None
        return votes >= max(1, total // 2)

    def latest_features(self):
        return {
            "close": round(self.close, 6),
            "atr": round(self.atr_value, 6),
            "atr_pct": round(self.atr_pct, 3),
            f"atr_pct_{ENTRY_TIMEFRAME}": round(self.atr_pct, 3),
            "rsi_14": round(self.rsi_value, 2),
            "adx_14": round(self.adx_value, 2),
            "volume_ratio": round(self.volume_ratio, 3),
            f"volume_ratio_{ENTRY_TIMEFRAME}": round(self.volume_ratio, 3),
            "ema20": round(self.ema20[-1], 6) if self.ema20 else 0,
            "ema50": round(self.ema50[-1], 6) if self.ema50 else 0,
            "vwap20": round(self.vwap20, 6),
            "trend_bias": self.trend_bias,
        }


def build_feature_context(candles, higher_timeframes=None):
    return FeatureContext(candles, higher_timeframes=higher_timeframes)


@dataclass
class StrategySignal:
    strategy: str
    action: str
    confidence: float
    entry_reason: str
    invalidation_reason: str
    recommended_stop_loss: Optional[float]
    recommended_take_profit: Optional[float]
    partial_profit_level: Optional[float]
    trailing_stop_logic: Dict
    works_best: List[str]
    rejection_reason: str = ""
    risk_reward: float = 0.0
    timeframe: str = ENTRY_TIMEFRAME
    metadata: Dict = field(default_factory=dict)

    @property
    def reason(self):
        return self.entry_reason if self.action != "hold" else self.rejection_reason

    def to_dict(self):
        data = asdict(self)
        data["reason"] = self.reason
        data["signal"] = self.action
        return data


class BaseProfessionalStrategy:
    name = "base"
    display_name = "Base"
    works_best = ["unknown"]

    def evaluate(self, features: FeatureContext) -> StrategySignal:
        raise NotImplementedError

    def _levels(self, f, stop_mult=2.0, tp_mult=3.2, partial_mult=1.5, trail_mult=2.0):
        # ATR floor: at least 1% of price to prevent noise stop-outs on thin markets
        atr_value = max(f.atr_value, f.close * 0.01)
        # Stop floor: never risk more than 8% from entry (matches proven Adaptive Breakout)
        max_stop_distance = f.close * 0.08
        stop_distance = min(atr_value * stop_mult, max_stop_distance)
        stop = max(f.close * 0.92, f.close - stop_distance)
        take = f.close + atr_value * tp_mult
        partial = f.close + atr_value * partial_mult
        risk = f.close - stop
        reward = take - f.close
        rr = reward / risk if risk > 0 else 0.0
        return stop, take, partial, {
            "type": "atr_trailing_stop",
            "activate_at": round(partial, 6),
            "atr_multiple": trail_mult,
            "initial_trail": round(max(0.000001, f.close - atr_value * trail_mult), 6),
        }, rr

    def _buy(self, f, confidence, reason, invalidation, stop_mult=2.0, tp_mult=3.2,
             partial_mult=1.5, trail_mult=2.0, metadata=None):
        stop, take, partial, trailing, rr = self._levels(
            f, stop_mult=stop_mult, tp_mult=tp_mult,
            partial_mult=partial_mult, trail_mult=trail_mult,
        )
        return StrategySignal(
            strategy=self.name,
            action="buy",
            confidence=round(_clamp(confidence), 3),
            entry_reason=reason,
            invalidation_reason=invalidation,
            recommended_stop_loss=round(stop, 6),
            recommended_take_profit=round(take, 6),
            partial_profit_level=round(partial, 6),
            trailing_stop_logic=trailing,
            works_best=list(self.works_best),
            risk_reward=round(rr, 2),
            metadata=metadata or {},
        )

    def _hold(self, f, confidence, rejection, invalidation=None, metadata=None):
        stop, take, partial, trailing, rr = self._levels(f)
        return StrategySignal(
            strategy=self.name,
            action="hold",
            confidence=round(_clamp(confidence), 3),
            entry_reason="No entry.",
            invalidation_reason=invalidation or "Setup did not confirm.",
            recommended_stop_loss=round(stop, 6) if f.close else None,
            recommended_take_profit=round(take, 6) if f.close else None,
            partial_profit_level=round(partial, 6) if f.close else None,
            trailing_stop_logic=trailing,
            works_best=list(self.works_best),
            rejection_reason=rejection,
            risk_reward=round(rr, 2),
            metadata=metadata or {},
        )

    def _insufficient(self, f):
        return self._hold(
            f,
            0.0,
            f"Insufficient {ENTRY_TIMEFRAME} candles ({len(f.candles)} loaded, need at least 60).",
            f"Need enough {ENTRY_TIMEFRAME} history before this bot can evaluate safely.",
        )


class TrendPullbackStrategy(BaseProfessionalStrategy):
    name = "trend_pullback"
    display_name = "Trend Pullback"
    works_best = ["trending", "risk_on", "bullish"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        ema20 = f.ema20[-1]
        ema50 = f.ema50[-1]
        trend_ok = f.close > ema50 and ema20 > ema50
        # Tighter pullback zone: price must actually touch or wick through EMA20,
        # not just be "near" it. The 0.3% tolerance handles bid-ask spread only.
        touched_zone = f.low <= ema20 * 1.003
        # RSI must show genuine pullback (not overbought) but not be crashing
        rsi_ok = 40 <= f.rsi_value <= 58
        # Require ADX to confirm a real trend exists (not just a mild drift)
        trend_strong = f.adx_value >= 20
        if trend_ok and touched_zone and rsi_ok and f.bullish_bar and trend_strong:
            conf = 0.54 + min(0.16, max(0, f.adx_value - 20) / 100) + min(0.08, (f.volume_ratio - 1) * 0.06)
            if f.htf_bullish is True:
                conf += 0.08
            return self._buy(
                f, conf,
                f"Trend pullback confirmed: price reclaimed EMA20/EMA50 zone, RSI {f.rsi_value:.1f}, ADX {f.adx_value:.1f}.",
                "Close below EMA50 or loss of pullback low.",
                stop_mult=2.0, tp_mult=2.5, partial_mult=1.3, trail_mult=1.8,
            )
        blockers = []
        if not trend_ok:
            blockers.append("1H trend is not bullish enough")
        if not touched_zone:
            blockers.append("price has not pulled back to the EMA value zone")
        if not rsi_ok:
            blockers.append(f"RSI {f.rsi_value:.1f} is outside pullback range")
        if not trend_strong:
            blockers.append(f"ADX {f.adx_value:.1f} too weak for trend confirmation")
        if not f.bullish_bar:
            blockers.append("latest 1H candle did not bounce convincingly")
        return self._hold(f, 0.34 if trend_ok else 0.18, "; ".join(blockers))


class EMACrossoverStrategy(BaseProfessionalStrategy):
    name = "ema_crossover"
    display_name = "EMA Crossover"
    works_best = ["trending", "breakout_ready", "risk_on"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        fast = f.ema9[-1]
        slow = f.ema21[-1]
        ema50 = f.ema50[-1]
        bullish_stack = fast > slow and f.close > ema50
        recent_cross = _crossed_above(f.ema9, f.ema21, lookback=3)
        # REQUIRE a fresh cross — just having EMAs stacked is not a signal.
        # ADX confirms trend strength but cannot substitute for the actual cross event.
        # Tightened: ADX >= 25 (was 18) and lookback=3 (was 6) to filter weak/stale crosses
        if bullish_stack and recent_cross and f.adx_value >= 25:
            sep = (fast - slow) / slow * 100 if slow else 0.0
            conf = 0.52 + min(0.15, sep * 0.08) + min(0.14, max(0, f.adx_value - 25) / 100)
            if f.htf_bullish is True:
                conf += 0.06
            return self._buy(
                f, conf,
                f"EMA crossover confirmed: EMA9 {fast:.2f} crossed above EMA21 {slow:.2f}, price above EMA50, ADX {f.adx_value:.1f}.",
                "EMA9 crosses back below EMA21 or price loses EMA50.",
                stop_mult=2.0, tp_mult=2.5, partial_mult=1.3, trail_mult=1.8,
            )
        return self._hold(
            f,
            0.32 if bullish_stack else 0.14,
            f"Waiting for fresh EMA9/EMA21 crossover with price above EMA50; now EMA9={fast:.2f}, EMA21={slow:.2f}, close={f.close:.2f}.",
            "EMA structure remains unconfirmed.",
        )


class MACDMomentumStrategy(BaseProfessionalStrategy):
    name = "macd_momentum"
    display_name = "MACD Momentum"
    works_best = ["trending", "risk_on", "bullish"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        hist = f.macd_histogram[-1]
        prev_hist = f.macd_histogram[-2] if len(f.macd_histogram) >= 2 else hist
        macd_now = f.macd_line[-1]
        signal_now = f.macd_signal[-1]
        # Check for fresh momentum: histogram must have been negative or near-zero
        # within recent bars (showing this is a NEW momentum move, not stale)
        recent_hist = f.macd_histogram[-6:] if len(f.macd_histogram) >= 6 else f.macd_histogram
        was_negative_recently = any(h <= 0 for h in recent_hist[:-1])
        histogram_fresh = was_negative_recently and hist > 0 and hist >= prev_hist
        if macd_now > signal_now and histogram_fresh and f.close > f.ema20[-1]:
            conf = 0.52 + min(0.16, abs(hist) / max(f.close, 1) * 120) + min(0.12, max(0, f.adx_value - 18) / 100)
            if f.volume_ratio >= 1.15:
                conf += 0.05
            return self._buy(
                f, conf,
                f"MACD fresh momentum: MACD {macd_now:.4f} > signal {signal_now:.4f}, histogram freshly positive and rising, close above EMA20.",
                "MACD histogram turns negative or price closes below EMA20.",
                stop_mult=2.0, tp_mult=2.5, partial_mult=1.3, trail_mult=1.8,
            )
        return self._hold(
            f,
            0.30 if hist > 0 else 0.12,
            f"MACD momentum not ready: MACD={macd_now:.4f}, signal={signal_now:.4f}, histogram={hist:.4f}, fresh={was_negative_recently if len(recent_hist) > 1 else 'N/A'}.",
        )


class RSIMeanReversionStrategy(BaseProfessionalStrategy):
    name = "rsi_mean_reversion"
    display_name = "RSI Mean Reversion"
    works_best = ["ranging", "sideways", "low_volatility"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        # Look back 8 bars (8 hours on 1H) for a genuine oversold wash
        recent_rsi = f.rsi14[-8:] if len(f.rsi14) >= 8 else f.rsi14
        min_rsi = min(recent_rsi) if recent_rsi else 50
        # Require clear recovery: RSI must have bounced at least 4 points AND
        # current RSI must be above 35 (confirming recovery, not still falling)
        recovering = f.rsi_value > min_rsi + 4.0 and f.rsi_value >= 35 and f.bullish_bar
        trend_floor = not f.ema200 or f.close >= f.ema200[-1] * 0.97
        # Require genuine oversold (RSI <= 30) not just "slightly low"
        if min_rsi <= 30 and recovering and trend_floor:
            conf = 0.50 + min(0.18, (32 - min_rsi) / 50) + (0.06 if f.bullish_bar else 0)
            if f.adx_value < 22:
                conf += 0.06
            return self._buy(
                f, conf,
                f"RSI reversion setup: RSI washed out to {min_rsi:.1f} and is recovering to {f.rsi_value:.1f}.",
                "RSI rolls back under the oversold low or price closes below recent swing low.",
                stop_mult=2.0, tp_mult=2.2, partial_mult=1.2, trail_mult=1.6,
            )
        return self._hold(
            f,
            0.33 if min_rsi <= 34 else 0.10,
            f"Waiting for oversold recovery: recent RSI low {min_rsi:.1f}, current RSI {f.rsi_value:.1f}, trend floor ok={trend_floor}.",
        )


class BollingerBandReversionStrategy(BaseProfessionalStrategy):
    name = "bollinger_reversion"
    display_name = "Bollinger Band Reversion"
    works_best = ["ranging", "sideways", "mean_reversion"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        lower = f.bb_lower[-1]
        mid = f.bb_mid[-1]
        band_width = (f.bb_upper[-1] - lower) / mid * 100 if mid else 0.0
        touched_lower = f.low <= lower * 1.002 or f.close <= lower * 1.005
        # Require RSI to confirm oversold, not just "slightly below average"
        if touched_lower and f.rsi_value <= 40 and f.close > f.open and f.adx_value < 25:
            conf = 0.50 + min(0.13, (40 - f.rsi_value) / 60) + (0.06 if f.adx_value < 20 else 0)
            return self._buy(
                f, conf,
                f"Bollinger reversion: price tagged lower band {lower:.2f}, RSI {f.rsi_value:.1f}, band width {band_width:.2f}%.",
                "Close below lower band continuation or failure to reclaim band midline.",
                stop_mult=2.0, tp_mult=2.2, partial_mult=1.2, trail_mult=1.6,
            )
        return self._hold(
            f,
            0.28 if touched_lower else 0.11,
            f"Waiting for lower-band rejection; close {f.close:.2f}, lower band {lower:.2f}, RSI {f.rsi_value:.1f}.",
        )


class BreakoutRetestStrategy(BaseProfessionalStrategy):
    name = "breakout_retest"
    display_name = "Breakout Retest"
    works_best = ["breakout_ready", "trending", "risk_on"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        # Use the Donchian 20-bar high (already computed with [1] offset) as the
        # resistance level — this is a proven, well-tested resistance measure
        # rather than a raw max of arbitrary lookback window.
        lookback_highs = f.highs[-30:-4]
        if not lookback_highs:
            return self._insufficient(f)
        # Use the second-to-last Donchian high if available (more stable than raw max)
        if len(f.donchian_high20) >= 5:
            level = f.donchian_high20[-5]  # resistance from 5 bars ago (well-established)
        else:
            level = max(lookback_highs)
        broke_recently = any(c > level for c in f.closes[-4:])
        retested = f.low <= level * 1.006 and f.close >= level
        if broke_recently and retested and f.volume_ratio >= 1.2 and f.adx_value >= 22:
            conf = 0.53 + min(0.14, max(0, f.volume_ratio - 1) * 0.10) + min(0.11, max(0, f.adx_value - 18) / 100)
            if f.htf_bullish is True:
                conf += 0.06
            return self._buy(
                f, conf,
                f"Breakout retest: level {level:.2f} broke and held on retest, volume {f.volume_ratio:.2f}x.",
                "Close back below retest level.",
                stop_mult=2.0, tp_mult=3.5, partial_mult=1.6, trail_mult=2.2,
            )
        return self._hold(
            f,
            0.36 if broke_recently else 0.15,
            f"Waiting for breakout plus retest: level {level:.2f}, broke_recently={broke_recently}, retested={retested}.",
        )


class DonchianChannelBreakoutStrategy(BaseProfessionalStrategy):
    name = "donchian_breakout"
    display_name = "Donchian Channel Breakout"
    works_best = ["breakout_ready", "trending", "volatile"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        upper = f.donchian_high20[-2] if len(f.donchian_high20) >= 2 else f.donchian_high20[-1]
        if f.close > upper and f.plus_di14[-1] > f.minus_di14[-1] and f.atr_pct >= 0.25 and f.adx_value >= 25:
            conf = 0.54 + min(0.15, max(0, f.adx_value - 25) / 80) + min(0.10, max(0, f.volume_ratio - 1) * 0.08)
            return self._buy(
                f, conf,
                f"Donchian breakout: close {f.close:.2f} cleared 20-bar high {upper:.2f}, +DI leads -DI, ADX {f.adx_value:.1f}.",
                "Close back inside Donchian channel or ADX deterioration.",
                stop_mult=2.5, tp_mult=4.0, partial_mult=2.0, trail_mult=2.5,
            )
        return self._hold(
            f,
            0.31 if f.close > upper * 0.985 else 0.10,
            f"No Donchian breakout: close {f.close:.2f}, 20-bar high {upper:.2f}, ADX {f.adx_value:.1f}.",
        )


class VWAPBounceStrategy(BaseProfessionalStrategy):
    name = "vwap_bounce"
    display_name = "VWAP Bounce"
    works_best = ["sideways", "ranging", "bullish"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        vwap = f.vwap20
        touched = f.low <= vwap * 1.004 and f.close >= vwap
        trend_ok = f.close >= f.ema50[-1] * 0.99
        if touched and trend_ok and f.bullish_bar:
            conf = 0.50 + min(0.12, max(0, f.volume_ratio - 1) * 0.10) + (0.04 if f.rsi_value >= 45 else 0)
            return self._buy(
                f, conf,
                f"VWAP bounce: price tested VWAP {vwap:.2f}, closed above it with volume {f.volume_ratio:.2f}x.",
                "Close below rolling VWAP after entry.",
                stop_mult=1.8, tp_mult=2.0, partial_mult=1.2, trail_mult=1.5,
            )
        return self._hold(
            f,
            0.30 if _near(f.close, vwap, 0.8) else 0.12,
            f"Waiting for VWAP bounce: close {f.close:.2f}, VWAP {vwap:.2f}, bullish_bar={f.bullish_bar}.",
        )


class ATRMomentumExpansionStrategy(BaseProfessionalStrategy):
    name = "atr_momentum_expansion"
    display_name = "ATR Momentum Expansion"
    works_best = ["volatile", "breakout_ready", "trending"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        ranges = [h - l for h, l in zip(f.highs, f.lows)]
        recent_range = ranges[-1] if ranges else 0.0
        avg_range = _mean(ranges[-20:-1])
        atr_rising = len(f.atr14) >= 6 and f.atr14[-1] > _mean(f.atr14[-6:-1])
        closes_near_high = f.high > 0 and f.close >= f.high - (recent_range * 0.25)
        trend_ok = f.close > f.ema20[-1]
        if avg_range > 0 and recent_range >= avg_range * 1.3 and atr_rising and closes_near_high and trend_ok:
            expansion = recent_range / avg_range
            conf = 0.53 + min(0.16, (expansion - 1) * 0.15) + min(0.10, max(0, f.volume_ratio - 1) * 0.08)
            return self._buy(
                f, conf,
                f"ATR momentum expansion: current range {expansion:.2f}x average, ATR rising, close near high.",
                "Momentum candle fails below its midpoint or ATR expansion reverses.",
                stop_mult=2.2, tp_mult=3.8, partial_mult=1.8, trail_mult=2.4,
            )
        return self._hold(
            f,
            0.29 if atr_rising else 0.10,
            f"Waiting for range expansion: range ratio {(recent_range / avg_range if avg_range else 0):.2f}x, ATR rising={atr_rising}, close near high={closes_near_high}.",
        )


class SupertrendContinuationStrategy(BaseProfessionalStrategy):
    name = "supertrend_continuation"
    display_name = "Supertrend Continuation"
    works_best = ["trending", "risk_on", "bullish"]

    def evaluate(self, f):
        if not f.enough:
            return self._insufficient(f)
        direction = f.supertrend_direction[-1] if f.supertrend_direction else 0
        line = f.supertrend_line[-1] if f.supertrend_line else 0.0
        pullback_to_line = line > 0 and f.low <= line * 1.012 and f.close > line
        trend_ok = direction > 0 and f.close > f.ema20[-1] > f.ema50[-1]
        # REQUIRE pullback to supertrend line — this is a continuation strategy,
        # not "buy anywhere in an uptrend". ADX confirms trend strength but the
        # pullback is the actual entry trigger.
        if trend_ok and pullback_to_line and f.adx_value >= 20 and f.bullish_bar:
            conf = 0.54 + min(0.16, max(0, f.adx_value - 20) / 80) + (0.05 if f.htf_bullish is True else 0)
            return self._buy(
                f, conf,
                f"Supertrend continuation: pullback to line {line:.2f}, bullish bounce, ADX {f.adx_value:.1f}, EMA stack positive.",
                "Close below supertrend line or EMA20/EMA50 trend break.",
                stop_mult=2.0, tp_mult=3.5, partial_mult=1.6, trail_mult=2.2,
            )
        return self._hold(
            f,
            0.33 if direction > 0 else 0.11,
            f"Waiting for bullish supertrend continuation: direction={direction}, line={line:.2f}, trend_ok={trend_ok}.",
        )


STRATEGY_CLASSES: Dict[str, Type[BaseProfessionalStrategy]] = {
    TrendPullbackStrategy.name: TrendPullbackStrategy,
    EMACrossoverStrategy.name: EMACrossoverStrategy,
    MACDMomentumStrategy.name: MACDMomentumStrategy,
    RSIMeanReversionStrategy.name: RSIMeanReversionStrategy,
    BollingerBandReversionStrategy.name: BollingerBandReversionStrategy,
    BreakoutRetestStrategy.name: BreakoutRetestStrategy,
    DonchianChannelBreakoutStrategy.name: DonchianChannelBreakoutStrategy,
    VWAPBounceStrategy.name: VWAPBounceStrategy,
    ATRMomentumExpansionStrategy.name: ATRMomentumExpansionStrategy,
    SupertrendContinuationStrategy.name: SupertrendContinuationStrategy,
}

# The ACTIVE strategy set (what the live registry builds bots for) is driven by
# config.PROFESSIONAL_STRATEGIES so strategies can be enabled/disabled without
# deleting their code. Fall back to all classes if config is missing/empty;
# unknown names are ignored.
_active_strategy_names = [n for n in getattr(config, "PROFESSIONAL_STRATEGIES", []) if n in STRATEGY_CLASSES]
STRATEGY_NAMES = _active_strategy_names or list(STRATEGY_CLASSES.keys())


def create_strategy(strategy_name):
    cls = STRATEGY_CLASSES.get(strategy_name)
    if not cls:
        raise KeyError(f"Unknown professional strategy: {strategy_name}")
    return cls()
