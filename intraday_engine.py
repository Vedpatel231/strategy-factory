"""
Intraday signal engine for Alpaca paper crypto trading.

This module is deliberately transparent: classic indicators, modular strategy
classes, confidence scores, and plain-English reasons. It is used as a quality
gate around the existing portfolio allocator rather than replacing the app.
"""

import json
import logging
import os
import statistics
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta

import config

logger = logging.getLogger("intraday_engine")

STATE_FILE = os.path.join(config.DATA_DIR, "intraday_state.json")

TRADE_TIMEFRAMES = ("15m", "30m", "1h")
SETUP_TIMEFRAME = "1h"
CONFIRM_TIMEFRAMES = ("4h", "1D")
MIN_SIGNAL_CONFIDENCE = float(os.environ.get("INTRADAY_MIN_SIGNAL_CONFIDENCE", "0.56"))
EXTREME_ATR_PCT = float(os.environ.get("INTRADAY_EXTREME_ATR_PCT", "8.0"))
MIN_VOLUME_RATIO = float(os.environ.get("INTRADAY_MIN_VOLUME_RATIO", "0.35"))


@dataclass
class Signal:
    strategy: str
    action: str
    confidence: float
    reason: str
    timeframe: str
    regime_fit: float = 1.0

    def weighted_confidence(self):
        return max(0.0, min(1.0, self.confidence * self.regime_fit))


@dataclass
class Regime:
    label: str
    confidence: float
    reason: str
    trend_bias: str
    atr_pct: float
    volume_ratio: float


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else 0.0


def _std(values):
    values = [v for v in values if v is not None]
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def ema(values, period):
    if not values:
        return []
    alpha = 2 / (period + 1)
    out = [float(values[0])]
    for value in values[1:]:
        out.append((float(value) * alpha) + (out[-1] * (1 - alpha)))
    return out


def sma(values, period):
    out = []
    for idx in range(len(values)):
        window = values[max(0, idx - period + 1):idx + 1]
        out.append(_mean(window))
    return out


def rsi(values, period=14):
    if len(values) < period + 1:
        return [50.0] * len(values)
    gains = []
    losses = []
    for idx in range(1, len(values)):
        change = values[idx] - values[idx - 1]
        gains.append(max(change, 0.0))
        losses.append(abs(min(change, 0.0)))
    rsis = [50.0] * min(period, len(values))
    avg_gain = _mean(gains[:period])
    avg_loss = _mean(losses[:period])
    for idx in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / period
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - (100 / (1 + rs)))
    return rsis[-len(values):]


def atr(candles, period=14):
    if not candles:
        return []
    trs = []
    prev_close = _safe_float(candles[0]["close"])
    for c in candles:
        high = _safe_float(c["high"])
        low = _safe_float(c["low"])
        close = _safe_float(c["close"])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        prev_close = close
    return sma(trs, period)


def bollinger(values, period=20, mult=2.0):
    mids = sma(values, period)
    upper = []
    lower = []
    for idx in range(len(values)):
        window = values[max(0, idx - period + 1):idx + 1]
        width = _std(window) * mult
        upper.append(mids[idx] + width)
        lower.append(mids[idx] - width)
    return lower, mids, upper


def _normalize_symbol_for_binance(symbol):
    return symbol.upper().replace("/", "").replace("USD", "USDT")


def _timeframe_to_binance(tf):
    return {"15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1D": "1d"}.get(tf, "15m")


def _timeframe_to_alpaca_rest(tf):
    return {"15m": "15Min", "30m": "30Min", "1h": "1Hour", "4h": "4Hour", "1D": "1Day"}.get(tf, "15Min")


class MarketDataProvider:
    def __init__(self, client=None):
        self.client = client

    def get_candles(self, symbol, timeframe, limit=160):
        candles = self._get_alpaca_candles(symbol, timeframe, limit)
        if candles:
            cleaned = self._clean(candles, limit)
            logger.info("Loaded %d %s candles for %s from alpaca_sdk", len(cleaned), timeframe, symbol)
            return cleaned
        candles = self._get_alpaca_rest_candles(symbol, timeframe, limit)
        if candles:
            cleaned = self._clean(candles, limit)
            logger.info("Loaded %d %s candles for %s from alpaca_rest", len(cleaned), timeframe, symbol)
            return cleaned
        candles = self._get_binance_candles(symbol, timeframe, limit)
        cleaned = self._clean(candles, limit)
        if cleaned:
            logger.info("Loaded %d %s candles for %s from binance", len(cleaned), timeframe, symbol)
        else:
            logger.warning("No candles loaded for %s %s from any provider", symbol, timeframe)
        return cleaned

    def _get_alpaca_candles(self, symbol, timeframe, limit):
        try:
            from alpaca.data.historical.crypto import CryptoHistoricalDataClient
            from alpaca.data.requests import CryptoBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            from alpaca_client import _ALPACA_KEY, _ALPACA_SECRET

            amount, unit = {
                "15m": (15, TimeFrameUnit.Minute),
                "30m": (30, TimeFrameUnit.Minute),
                "1h": (1, TimeFrameUnit.Hour),
                "4h": (4, TimeFrameUnit.Hour),
                "1D": (1, TimeFrameUnit.Day),
            }.get(timeframe, (15, TimeFrameUnit.Minute))
            data_client = CryptoHistoricalDataClient(
                api_key=_ALPACA_KEY,
                secret_key=_ALPACA_SECRET,
            )
            # Alpaca's historical endpoint is more reliable when bounded by a
            # start/end window instead of relying on limit alone.
            end = datetime.now(timezone.utc)
            lookback = {
                "15m": timedelta(days=4),
                "30m": timedelta(days=8),
                "1h": timedelta(days=14),
                "4h": timedelta(days=45),
                "1D": timedelta(days=220),
            }.get(timeframe, timedelta(days=4))
            req = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(amount, unit),
                start=end - lookback,
                end=end,
                limit=limit,
            )
            bars = data_client.get_crypto_bars(req)
            raw = []
            if hasattr(bars, "data"):
                raw = bars.data.get(symbol, []) or bars.data.get(symbol.replace("/", ""), [])
            elif isinstance(bars, dict):
                raw = bars.get(symbol, []) or bars.get(symbol.replace("/", ""), [])
            elif hasattr(bars, "df"):
                try:
                    df = bars.df
                    if getattr(df, "empty", True):
                        raw = []
                    else:
                        if hasattr(df.index, "names") and "symbol" in df.index.names:
                            df = df.xs(symbol, level="symbol")
                        raw = [
                            {
                                "timestamp": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
                                "open": row["open"],
                                "high": row["high"],
                                "low": row["low"],
                                "close": row["close"],
                                "volume": row.get("volume", 0.0),
                            }
                            for idx, row in df.tail(limit).iterrows()
                        ]
                except Exception:
                    raw = []
            out = []
            for b in raw:
                if isinstance(b, dict):
                    out.append({
                        "timestamp": b.get("timestamp", ""),
                        "open": float(b.get("open")),
                        "high": float(b.get("high")),
                        "low": float(b.get("low")),
                        "close": float(b.get("close")),
                        "volume": float(b.get("volume", 0.0) or 0.0),
                    })
                else:
                    out.append({
                        "timestamp": getattr(b, "timestamp", None).isoformat()
                        if getattr(b, "timestamp", None) else "",
                        "open": float(getattr(b, "open")),
                        "high": float(getattr(b, "high")),
                        "low": float(getattr(b, "low")),
                        "close": float(getattr(b, "close")),
                        "volume": float(getattr(b, "volume", 0.0) or 0.0),
                    })
            return out
        except Exception as exc:
            logger.warning("Alpaca SDK candles unavailable for %s %s: %s", symbol, timeframe, exc)
            return []

    def _get_alpaca_rest_candles(self, symbol, timeframe, limit):
        try:
            import requests
            from alpaca_client import _ALPACA_KEY, _ALPACA_SECRET

            end = datetime.now(timezone.utc)
            lookback = {
                "15m": timedelta(days=4),
                "30m": timedelta(days=8),
                "1h": timedelta(days=14),
                "4h": timedelta(days=45),
                "1D": timedelta(days=220),
            }.get(timeframe, timedelta(days=4))
            params = {
                "symbols": symbol,
                "timeframe": _timeframe_to_alpaca_rest(timeframe),
                "start": (end - lookback).isoformat().replace("+00:00", "Z"),
                "end": end.isoformat().replace("+00:00", "Z"),
                "limit": limit,
                "sort": "asc",
            }
            headers = {
                "APCA-API-KEY-ID": _ALPACA_KEY,
                "APCA-API-SECRET-KEY": _ALPACA_SECRET,
            }
            resp = requests.get(
                "https://data.alpaca.markets/v1beta3/crypto/us/bars",
                params=params,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            raw = (data.get("bars") or {}).get(symbol, [])
            return [
                {
                    "timestamp": b.get("t", ""),
                    "open": b.get("o"),
                    "high": b.get("h"),
                    "low": b.get("l"),
                    "close": b.get("c"),
                    "volume": b.get("v", 0.0),
                }
                for b in raw
            ]
        except Exception as exc:
            logger.warning("Alpaca REST candles unavailable for %s %s: %s", symbol, timeframe, exc)
            return []

    def _get_binance_candles(self, symbol, timeframe, limit):
        try:
            from api_client import StrategyFactoryClient
            client = StrategyFactoryClient()
            pair = _normalize_symbol_for_binance(symbol)
            return client.get_market_data(pair, interval=_timeframe_to_binance(timeframe), limit=limit)
        except Exception as exc:
            logger.debug("Binance candles unavailable for %s %s: %s", symbol, timeframe, exc)
            return []

    def _clean(self, candles, limit):
        out = []
        last_ts = None
        for c in candles[-limit:]:
            close = _safe_float(c.get("close"))
            high = _safe_float(c.get("high"))
            low = _safe_float(c.get("low"))
            open_ = _safe_float(c.get("open"))
            if close <= 0 or high <= 0 or low <= 0 or high < low:
                continue
            ts = c.get("timestamp", "")
            if ts and ts == last_ts:
                continue
            last_ts = ts
            out.append({
                "timestamp": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": max(0.0, _safe_float(c.get("volume"))),
            })
        return out


class FeatureSet:
    def __init__(self, candles):
        self.candles = candles
        self.closes = [c["close"] for c in candles]
        self.highs = [c["high"] for c in candles]
        self.lows = [c["low"] for c in candles]
        self.volumes = [c["volume"] for c in candles]
        self.close = self.closes[-1] if self.closes else 0.0
        self.ema9 = ema(self.closes, 9)
        self.ema20 = ema(self.closes, 20)
        self.ema50 = ema(self.closes, 50)
        self.rsi14 = rsi(self.closes, 14)
        self.atr14 = atr(candles, 14)
        self.bb_low, self.bb_mid, self.bb_high = bollinger(self.closes, 20, 2.0)

    @property
    def atr_pct(self):
        return (self.atr14[-1] / self.close * 100.0) if self.close and self.atr14 else 0.0

    @property
    def volume_ratio(self):
        recent = _mean(self.volumes[-5:])
        base = _mean(self.volumes[-40:-5]) if len(self.volumes) >= 45 else _mean(self.volumes[:-5])
        return recent / base if base > 0 else 1.0

    @property
    def ema20_slope_pct(self):
        if len(self.ema20) < 8 or not self.ema20[-8]:
            return 0.0
        return (self.ema20[-1] - self.ema20[-8]) / self.ema20[-8] * 100.0

    @property
    def bb_width_pct(self):
        if not self.close or not self.bb_high:
            return 0.0
        return (self.bb_high[-1] - self.bb_low[-1]) / self.close * 100.0


class RegimeDetector:
    def classify(self, features):
        if len(features.closes) < 60:
            return Regime("unknown", 0.0, "Insufficient candles", "neutral",
                          features.atr_pct, features.volume_ratio)

        close = features.close
        ema20 = features.ema20[-1]
        ema50 = features.ema50[-1]
        slope = features.ema20_slope_pct
        atr_pct = features.atr_pct
        vol_ratio = features.volume_ratio
        bb_width = features.bb_width_pct

        trend_bias = "neutral"
        if close > ema20 > ema50 and slope > 0.12:
            trend_bias = "up"
        elif close < ema20 < ema50 and slope < -0.12:
            trend_bias = "down"

        if atr_pct >= EXTREME_ATR_PCT:
            return Regime("extreme_volatility", 0.9, f"ATR {atr_pct:.1f}% exceeds no-trade limit", trend_bias, atr_pct, vol_ratio)
        if vol_ratio > 1.35 and close > features.bb_high[-1] and trend_bias == "up":
            return Regime("breakout", 0.78, "Close above upper band with elevated volume", trend_bias, atr_pct, vol_ratio)
        if vol_ratio > 1.35 and close < features.bb_low[-1] and trend_bias == "down":
            return Regime("breakdown", 0.78, "Close below lower band with elevated volume", trend_bias, atr_pct, vol_ratio)
        if trend_bias == "up":
            return Regime("trending_up", min(0.85, 0.55 + abs(slope) / 2), "Price above rising 20/50 EMA stack", trend_bias, atr_pct, vol_ratio)
        if trend_bias == "down":
            return Regime("trending_down", min(0.85, 0.55 + abs(slope) / 2), "Price below falling 20/50 EMA stack", trend_bias, atr_pct, vol_ratio)
        if atr_pct > 4.0 or bb_width > 10.0:
            return Regime("high_volatility", 0.7, "Wide range without clean trend", trend_bias, atr_pct, vol_ratio)
        if atr_pct < 1.0 and bb_width < 3.0:
            return Regime("low_volatility", 0.68, "Compressed range and low ATR", trend_bias, atr_pct, vol_ratio)
        if 42 <= features.rsi14[-1] <= 58 and abs(slope) < 0.08:
            return Regime("range_bound", 0.66, "Flat EMA and mid-range RSI", trend_bias, atr_pct, vol_ratio)
        return Regime("choppy", 0.58, "Mixed trend, volatility, and momentum signals", trend_bias, atr_pct, vol_ratio)


class BaseStrategy:
    name = "base"
    good_regimes = set()
    bad_regimes = set()

    def regime_fit(self, regime):
        if regime.label in self.good_regimes:
            return 1.15
        if regime.label in self.bad_regimes:
            return 0.55
        return 0.9

    def evaluate(self, features, regime, timeframe):
        return Signal(self.name, "hold", 0.0, "No setup", timeframe, self.regime_fit(regime))


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"
    good_regimes = {"trending_up", "breakout"}
    bad_regimes = {"range_bound", "choppy"}

    def evaluate(self, f, regime, timeframe):
        if f.close > f.ema20[-1] > f.ema50[-1] and f.ema20_slope_pct > 0.12 and f.rsi14[-1] < 75:
            return Signal(self.name, "buy", 0.64, "Rising EMA stack with RSI below exhaustion", timeframe, self.regime_fit(regime))
        if f.close < f.ema20[-1] < f.ema50[-1] and f.ema20_slope_pct < -0.12:
            return Signal(self.name, "sell", 0.62, "Falling EMA stack", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class MomentumStrategy(BaseStrategy):
    name = "momentum"
    good_regimes = {"trending_up", "breakout"}
    bad_regimes = {"low_volatility", "range_bound"}

    def evaluate(self, f, regime, timeframe):
        if f.rsi14[-1] > 56 and f.close > f.ema20[-1] and f.volume_ratio > 0.8:
            return Signal(self.name, "buy", min(0.75, 0.52 + (f.rsi14[-1] - 56) / 100), "Positive RSI momentum above EMA20", timeframe, self.regime_fit(regime))
        if f.rsi14[-1] < 42 and f.close < f.ema20[-1]:
            return Signal(self.name, "sell", 0.6, "Negative RSI momentum below EMA20", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class BreakoutStrategy(BaseStrategy):
    name = "breakout"
    good_regimes = {"breakout", "low_volatility"}
    bad_regimes = {"choppy", "high_volatility"}

    def evaluate(self, f, regime, timeframe):
        if f.close > f.bb_high[-1] and f.volume_ratio >= 1.2:
            return Signal(self.name, "buy", min(0.82, 0.58 + (f.volume_ratio - 1.0) / 3), "Upper-band breakout with volume expansion", timeframe, self.regime_fit(regime))
        if f.close < f.bb_low[-1] and f.volume_ratio >= 1.2:
            return Signal(self.name, "sell", 0.66, "Lower-band breakdown with volume expansion", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"
    good_regimes = {"range_bound", "choppy"}
    bad_regimes = {"trending_down", "breakdown", "breakout"}

    def evaluate(self, f, regime, timeframe):
        if f.close < f.bb_low[-1] and f.rsi14[-1] < 34 and regime.trend_bias != "down":
            return Signal(self.name, "buy", 0.62, "Oversold below lower band without downtrend bias", timeframe, self.regime_fit(regime))
        if f.close > f.bb_high[-1] and f.rsi14[-1] > 70:
            return Signal(self.name, "sell", 0.6, "Overbought above upper band", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class PullbackContinuationStrategy(BaseStrategy):
    name = "pullback_continuation"
    good_regimes = {"trending_up"}
    bad_regimes = {"range_bound", "breakdown"}

    def evaluate(self, f, regime, timeframe):
        near_ema = abs(f.close - f.ema20[-1]) / f.close * 100 <= max(0.8, f.atr_pct * 0.45)
        if regime.trend_bias == "up" and near_ema and 42 <= f.rsi14[-1] <= 62:
            return Signal(self.name, "buy", 0.65, "Pullback to rising EMA20 in uptrend", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class VolatilityBreakoutStrategy(BaseStrategy):
    name = "volatility_breakout"
    good_regimes = {"low_volatility", "breakout"}
    bad_regimes = {"extreme_volatility", "choppy"}

    def evaluate(self, f, regime, timeframe):
        recent_range = max(f.highs[-8:]) - min(f.lows[-8:])
        if f.atr14[-1] and recent_range / f.close * 100 < max(1.2, f.atr_pct * 1.6):
            if f.close >= max(f.highs[-8:-1]) and f.volume_ratio > 1.1:
                return Signal(self.name, "buy", 0.63, "Compression resolving upward with volume", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class RangeTradingStrategy(BaseStrategy):
    name = "range_trading"
    good_regimes = {"range_bound", "low_volatility"}
    bad_regimes = {"trending_up", "trending_down", "breakout", "breakdown"}

    def evaluate(self, f, regime, timeframe):
        if regime.label in self.good_regimes and f.rsi14[-1] < 38 and f.close <= f.bb_mid[-1]:
            return Signal(self.name, "buy", 0.58, "Range buy near lower half with weak RSI", timeframe, self.regime_fit(regime))
        if regime.label in self.good_regimes and f.rsi14[-1] > 66:
            return Signal(self.name, "sell", 0.58, "Range fade near upper half", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class SwingTradingStrategy(BaseStrategy):
    name = "swing_trading"
    good_regimes = {"trending_up", "range_bound"}
    bad_regimes = {"extreme_volatility"}

    def evaluate(self, f, regime, timeframe):
        higher_low = len(f.lows) >= 6 and f.lows[-1] > min(f.lows[-5:-1])
        if higher_low and f.close > f.ema20[-1] and 48 <= f.rsi14[-1] <= 68:
            return Signal(self.name, "buy", 0.57, "Higher-low structure reclaimed EMA20", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class ReversalStructureStrategy(BaseStrategy):
    name = "reversal_market_structure"
    good_regimes = {"range_bound", "high_volatility"}
    bad_regimes = {"trending_down"}

    def evaluate(self, f, regime, timeframe):
        if len(f.closes) >= 5:
            made_low = f.lows[-2] <= min(f.lows[-8:-2])
            reclaimed = f.close > f.highs[-2] and f.rsi14[-1] > 42
            if made_low and reclaimed and regime.trend_bias != "down":
                return Signal(self.name, "buy", 0.6, "Failed breakdown and reclaim of prior candle high", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


class GridRangeStrategy(BaseStrategy):
    name = "grid_range"
    good_regimes = {"range_bound", "low_volatility"}
    bad_regimes = {"trending_down", "breakout", "breakdown", "extreme_volatility"}

    def evaluate(self, f, regime, timeframe):
        if regime.label in self.good_regimes and f.bb_width_pct < 5.0:
            if f.close < f.bb_mid[-1] and f.rsi14[-1] < 48:
                return Signal(self.name, "buy", 0.55, "Low-volatility grid entry below range midpoint", timeframe, self.regime_fit(regime))
        return super().evaluate(f, regime, timeframe)


STRATEGIES = [
    TrendFollowingStrategy(),
    MomentumStrategy(),
    BreakoutStrategy(),
    MeanReversionStrategy(),
    PullbackContinuationStrategy(),
    VolatilityBreakoutStrategy(),
    RangeTradingStrategy(),
    SwingTradingStrategy(),
    ReversalStructureStrategy(),
    GridRangeStrategy(),
]


class IntradaySignalEngine:
    def __init__(self, data_provider=None):
        self.data = data_provider or MarketDataProvider()
        self.regime_detector = RegimeDetector()

    def evaluate_symbol(self, symbol):
        frame_data = {}
        ordered_frames = []
        for tf in (*TRADE_TIMEFRAMES, *CONFIRM_TIMEFRAMES):
            if tf not in ordered_frames:
                ordered_frames.append(tf)
        for tf in ordered_frames:
            candles = self.data.get_candles(symbol, tf)
            if len(candles) < 60:
                return self._reject(symbol, f"Insufficient {tf} candles ({len(candles)})")
            frame_data[tf] = FeatureSet(candles)

        setup_regime = self.regime_detector.classify(frame_data[SETUP_TIMEFRAME])
        trade_regime = self.regime_detector.classify(frame_data[TRADE_TIMEFRAMES[0]])
        confirm_regimes = {
            tf: self.regime_detector.classify(frame_data[tf])
            for tf in CONFIRM_TIMEFRAMES
        }
        primary_confirm = confirm_regimes[CONFIRM_TIMEFRAMES[0]]

        if trade_regime.label == "extreme_volatility" or setup_regime.label == "extreme_volatility":
            return self._reject(symbol, trade_regime.reason or setup_regime.reason,
                                trade_regime, setup_regime, primary_confirm, confirm_regimes)
        if frame_data[TRADE_TIMEFRAMES[0]].volume_ratio < MIN_VOLUME_RATIO:
            return self._reject(symbol, "Liquidity/volume ratio below minimum",
                                trade_regime, setup_regime, primary_confirm, confirm_regimes)

        signals = []
        for tf in TRADE_TIMEFRAMES:
            f = frame_data[tf]
            regime = self.regime_detector.classify(f)
            for strategy in STRATEGIES:
                sig = strategy.evaluate(f, regime, tf)
                if sig.action != "hold":
                    signals.append(sig)

        buy_score = sum(s.weighted_confidence() for s in signals if s.action == "buy")
        sell_score = sum(s.weighted_confidence() for s in signals if s.action == "sell")
        total_score = buy_score + sell_score
        direction = "hold"
        confidence = 0.0
        if total_score > 0:
            if buy_score > sell_score:
                direction = "buy"
                confidence = buy_score / total_score
            else:
                direction = "sell"
                confidence = sell_score / total_score

        alignment_penalty = 0.0
        for tf, confirm_regime in confirm_regimes.items():
            if direction == "buy" and confirm_regime.trend_bias == "down":
                alignment_penalty += 0.12 if tf == "4h" else 0.10
            if direction == "sell" and confirm_regime.trend_bias == "up":
                alignment_penalty += 0.08 if tf == "4h" else 0.06
        confidence = max(0.0, confidence - alignment_penalty)

        reasons = sorted(signals, key=lambda s: s.weighted_confidence(), reverse=True)[:5]
        accepted = direction in ("buy", "sell") and confidence >= MIN_SIGNAL_CONFIDENCE
        if direction == "sell" and accepted:
            # Alpaca crypto path is long-only here; sell means close/downweight,
            # not open a new short.
            accepted = True

        result = {
            "symbol": symbol,
            "accepted": accepted,
            "action": direction,
            "confidence": round(confidence, 3),
            "reason": "; ".join(s.reason for s in reasons) if reasons else "No strong intraday setup",
            "strategy_signals": [asdict(s) for s in signals],
            "trade_regime": asdict(trade_regime),
            "setup_regime": asdict(setup_regime),
            "confirm_regime": asdict(primary_confirm),
            "confirm_regimes": {tf: asdict(regime) for tf, regime in confirm_regimes.items()},
            "features": {
                "atr_pct_15m": round(frame_data["15m"].atr_pct, 3),
                "volume_ratio_15m": round(frame_data["15m"].volume_ratio, 3),
                "volume_ratio_30m": round(frame_data["30m"].volume_ratio, 3),
                "ema20_slope_1h": round(frame_data["1h"].ema20_slope_pct, 3),
            },
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_last(result)
        return result

    def _reject(self, symbol, reason, trade_regime=None, setup_regime=None, confirm_regime=None, confirm_regimes=None):
        result = {
            "symbol": symbol,
            "accepted": False,
            "action": "hold",
            "confidence": 0.0,
            "reason": reason,
            "strategy_signals": [],
            "trade_regime": asdict(trade_regime) if trade_regime else {},
            "setup_regime": asdict(setup_regime) if setup_regime else {},
            "confirm_regime": asdict(confirm_regime) if confirm_regime else {},
            "confirm_regimes": {tf: asdict(regime) for tf, regime in (confirm_regimes or {}).items()},
            "features": {},
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_last(result)
        return result

    def _save_last(self, result):
        try:
            os.makedirs(config.DATA_DIR, exist_ok=True)
            state = {}
            if os.path.exists(STATE_FILE):
                with open(STATE_FILE) as f:
                    state = json.load(f)
            state[result["symbol"]] = result
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2, default=str)
        except Exception:
            logger.debug("Could not persist intraday state", exc_info=True)


def load_intraday_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}
