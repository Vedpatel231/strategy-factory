"""
Adaptive Breakout signal engine for Alpaca trading.

Single strategy: Donchian channel breakout + ADX regime filter on 4h timeframe.
Proven profitable on TradingView with real data:
  BTC +99%, ETH +142%, SOL +121%, TSLA +113%, AAPL +3.3%

This module replaces the old multi-strategy EMA crossover engine.
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

# Strategy runs on 4h only — no multi-timeframe cross-confirmation needed
STRATEGY_TIMEFRAME = config.STRATEGY_TIMEFRAME  # "4h"


@dataclass
class Signal:
    strategy: str
    action: str
    confidence: float
    reason: str
    timeframe: str
    adx: float = 0.0
    atr_pct: float = 0.0

    def weighted_confidence(self):
        return max(0.0, min(1.0, self.confidence))


@dataclass
class Regime:
    label: str
    adx: float
    plus_di: float
    minus_di: float
    atr_pct: float
    reason: str


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
    """Average True Range using Wilder's smoothing (matches TradingView)."""
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

    if len(trs) < period:
        return sma(trs, period)

    # Wilder's smoothing: first ATR = SMA of first `period` TRs
    atr_vals = [0.0] * (period - 1)
    first_atr = sum(trs[:period]) / period
    atr_vals.append(first_atr)
    for i in range(period, len(trs)):
        atr_vals.append((atr_vals[-1] * (period - 1) + trs[i]) / period)
    return atr_vals


def donchian_high(highs, period=20):
    """Donchian channel upper band: highest high over past `period` bars.
    Uses [1] offset — looks at bars BEFORE the current one (matches Pine: highest(high, 20)[1])."""
    out = []
    for i in range(len(highs)):
        # [1] offset: look at bars from i-period to i-1 (excluding current bar)
        start = max(0, i - period)
        end = i  # exclusive — does NOT include current bar
        if end <= start:
            out.append(highs[i])  # not enough history, use current
        else:
            out.append(max(highs[start:end]))
    return out


def donchian_low(lows, period=20):
    """Donchian channel lower band: lowest low over past `period` bars.
    Uses [1] offset (matches Pine: lowest(low, 20)[1])."""
    out = []
    for i in range(len(lows)):
        start = max(0, i - period)
        end = i
        if end <= start:
            out.append(lows[i])
        else:
            out.append(min(lows[start:end]))
    return out


def _wilder_smooth(values, period):
    """Wilder's smoothing method — critical for matching TradingView ADX values."""
    if len(values) < period:
        return sma(values, period)
    out = [0.0] * (period - 1)
    first = sum(values[:period]) / period
    out.append(first)
    for i in range(period, len(values)):
        out.append((out[-1] * (period - 1) + values[i]) / period)
    return out


def adx_system(candles, period=14):
    """Compute ADX, +DI, -DI using Wilder's smoothing (matches TradingView exactly).

    Returns: (adx_values, plus_di_values, minus_di_values) — all same length as candles.
    """
    if len(candles) < period + 1:
        n = len(candles)
        return [0.0] * n, [0.0] * n, [0.0] * n

    # Step 1: True Range and Directional Movement
    trs = [0.0]
    plus_dm = [0.0]
    minus_dm = [0.0]
    for i in range(1, len(candles)):
        high = _safe_float(candles[i]["high"])
        low = _safe_float(candles[i]["low"])
        prev_high = _safe_float(candles[i - 1]["high"])
        prev_low = _safe_float(candles[i - 1]["low"])
        prev_close = _safe_float(candles[i - 1]["close"])

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

        up_move = high - prev_high
        down_move = prev_low - low

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)

    # Step 2: Wilder's smoothing of TR, +DM, -DM
    smoothed_tr = _wilder_smooth(trs, period)
    smoothed_plus_dm = _wilder_smooth(plus_dm, period)
    smoothed_minus_dm = _wilder_smooth(minus_dm, period)

    # Step 3: +DI and -DI
    plus_di_vals = []
    minus_di_vals = []
    for i in range(len(smoothed_tr)):
        tr_val = smoothed_tr[i]
        if tr_val > 0:
            plus_di_vals.append(smoothed_plus_dm[i] / tr_val * 100.0)
            minus_di_vals.append(smoothed_minus_dm[i] / tr_val * 100.0)
        else:
            plus_di_vals.append(0.0)
            minus_di_vals.append(0.0)

    # Step 4: DX and ADX
    dx_vals = []
    for i in range(len(plus_di_vals)):
        di_sum = plus_di_vals[i] + minus_di_vals[i]
        if di_sum > 0:
            dx_vals.append(abs(plus_di_vals[i] - minus_di_vals[i]) / di_sum * 100.0)
        else:
            dx_vals.append(0.0)

    # Step 5: Smooth DX to get ADX (another round of Wilder's smoothing)
    adx_vals = _wilder_smooth(dx_vals, period)

    return adx_vals, plus_di_vals, minus_di_vals


def _normalize_symbol_for_binance(symbol):
    return symbol.upper().replace("/", "").replace("USD", "USDT")


def _timeframe_to_binance(tf):
    return {"15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h", "1D": "1d"}.get(tf, "4h")


def _timeframe_to_alpaca_rest(tf):
    return {"15m": "15Min", "30m": "30Min", "1h": "1Hour", "4h": "4Hour", "1D": "1Day"}.get(tf, "4Hour")


class MarketDataProvider:
    def __init__(self, client=None):
        self.client = client

    def get_candles(self, symbol, timeframe, limit=160):
        """Get candles — routes to crypto or stock provider based on symbol."""
        from alpaca_client import is_equity_symbol
        if is_equity_symbol(symbol):
            candles = self._get_stock_candles(symbol, timeframe, limit)
            if candles:
                cleaned = self._clean(candles, limit)
                logger.info("Loaded %d %s candles for %s from stock_sdk", len(cleaned), timeframe, symbol)
                return cleaned
            logger.warning("No stock candles loaded for %s %s", symbol, timeframe)
            return []

        # Crypto: try Alpaca SDK → Alpaca REST → Binance
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

    def _get_stock_candles(self, symbol, timeframe, limit):
        """Fetch stock bars from Alpaca StockHistoricalDataClient."""
        try:
            from alpaca.data.historical.stock import StockHistoricalDataClient
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
            from alpaca_client import _ALPACA_KEY, _ALPACA_SECRET

            amount, unit = {
                "15m": (15, TimeFrameUnit.Minute),
                "30m": (30, TimeFrameUnit.Minute),
                "1h": (1, TimeFrameUnit.Hour),
                "4h": (4, TimeFrameUnit.Hour),
                "1D": (1, TimeFrameUnit.Day),
            }.get(timeframe, (4, TimeFrameUnit.Hour))

            data_client = StockHistoricalDataClient(
                api_key=_ALPACA_KEY,
                secret_key=_ALPACA_SECRET,
            )
            end = datetime.now(timezone.utc)
            lookback = {
                "15m": timedelta(days=4),
                "30m": timedelta(days=8),
                "1h": timedelta(days=14),
                "4h": timedelta(days=90),
                "1D": timedelta(days=365),
            }.get(timeframe, timedelta(days=90))

            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(amount, unit),
                start=end - lookback,
                end=end,
                limit=limit,
            )
            bars = data_client.get_stock_bars(req)
            return self._parse_alpaca_bars(bars, symbol, limit)
        except Exception as exc:
            logger.warning("Stock candles unavailable for %s %s: %s", symbol, timeframe, exc)
            return []

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
            }.get(timeframe, (4, TimeFrameUnit.Hour))
            data_client = CryptoHistoricalDataClient(
                api_key=_ALPACA_KEY,
                secret_key=_ALPACA_SECRET,
            )
            end = datetime.now(timezone.utc)
            lookback = {
                "15m": timedelta(days=4),
                "30m": timedelta(days=8),
                "1h": timedelta(days=14),
                "4h": timedelta(days=90),
                "1D": timedelta(days=365),
            }.get(timeframe, timedelta(days=90))
            req = CryptoBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=TimeFrame(amount, unit),
                start=end - lookback,
                end=end,
                limit=limit,
            )
            bars = data_client.get_crypto_bars(req)
            return self._parse_alpaca_bars(bars, symbol, limit)
        except Exception as exc:
            logger.warning("Alpaca SDK candles unavailable for %s %s: %s", symbol, timeframe, exc)
            return []

    def _parse_alpaca_bars(self, bars, symbol, limit):
        """Parse Alpaca bar response into our standard candle format."""
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

    def _get_alpaca_rest_candles(self, symbol, timeframe, limit):
        try:
            import requests
            from alpaca_client import _ALPACA_KEY, _ALPACA_SECRET

            end = datetime.now(timezone.utc)
            lookback = {
                "15m": timedelta(days=4),
                "30m": timedelta(days=8),
                "1h": timedelta(days=14),
                "4h": timedelta(days=90),
                "1D": timedelta(days=365),
            }.get(timeframe, timedelta(days=90))
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
    """Pre-computed indicators for the Adaptive Breakout strategy."""

    def __init__(self, candles):
        self.candles = candles
        self.closes = [c["close"] for c in candles]
        self.highs = [c["high"] for c in candles]
        self.lows = [c["low"] for c in candles]
        self.volumes = [c["volume"] for c in candles]
        self.close = self.closes[-1] if self.closes else 0.0

        # Core indicators
        self.atr14 = atr(candles, 14)
        self.rsi14 = rsi(self.closes, 14)

        # Donchian channels (20-period, [1] offset)
        self.donchian_high_20 = donchian_high(self.highs, config.DONCHIAN_PERIOD)
        self.donchian_low_20 = donchian_low(self.lows, config.DONCHIAN_PERIOD)

        # ADX system with Wilder's smoothing
        self.adx_14, self.plus_di_14, self.minus_di_14 = adx_system(candles, config.ADX_PERIOD)

        # EMAs for trend context
        self.ema20 = ema(self.closes, 20)
        self.ema50 = ema(self.closes, 50)

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


class AdaptiveBreakoutStrategy:
    """Donchian channel breakout + ADX regime filter.

    Entry: close > donchian_high(20)[1] AND ADX(14) > 20 AND +DI > -DI
    Exit: trailing stop (3x ATR) OR hard stop (8%) OR ADX < 15
    Long only — shorts proven unprofitable on all tested assets.

    Proven on TradingView (real data, 4h timeframe):
      BTC: +99.2%, 76 trades, 38.2% WR, 1.316 PF
      ETH: +141.8%, 62 trades, 40.3% WR, 1.418 PF
      SOL: +120.8%, 83 trades, 41.0% WR, 1.152 PF
      TSLA: +113.1%, 19 trades, 42.1% WR, 2.251 PF
    """
    name = "adaptive_breakout"

    def evaluate(self, features, is_stock=False):
        """Evaluate for entry signal. Returns Signal or None."""
        f = features
        n = len(f.closes)
        if n < 40:
            return Signal(self.name, "hold", 0.0, "Insufficient candles", STRATEGY_TIMEFRAME)

        close = f.close
        adx_val = f.adx_14[-1] if f.adx_14 else 0
        plus_di = f.plus_di_14[-1] if f.plus_di_14 else 0
        minus_di = f.minus_di_14[-1] if f.minus_di_14 else 0
        atr_pct = f.atr_pct
        donch_high = f.donchian_high_20[-1] if f.donchian_high_20 else 0

        # Min volatility filter
        min_atr = config.STOCK_MIN_ATR_PCT if is_stock else config.CRYPTO_MIN_ATR_PCT
        if atr_pct < min_atr:
            return Signal(self.name, "hold", 0.0,
                          f"ATR% {atr_pct:.2f} below min {min_atr}", STRATEGY_TIMEFRAME)

        # ADX regime check — only trade in trending markets
        if adx_val < config.ADX_ENTRY_THRESHOLD:
            return Signal(self.name, "hold", 0.0,
                          f"ADX {adx_val:.1f} below entry threshold {config.ADX_ENTRY_THRESHOLD}",
                          STRATEGY_TIMEFRAME, adx=adx_val, atr_pct=atr_pct)

        # Directional check — +DI must lead -DI for longs
        if plus_di <= minus_di:
            return Signal(self.name, "hold", 0.0,
                          f"+DI ({plus_di:.1f}) not above -DI ({minus_di:.1f})",
                          STRATEGY_TIMEFRAME, adx=adx_val, atr_pct=atr_pct)

        # Donchian breakout: close > previous highest high
        if close <= donch_high:
            return Signal(self.name, "hold", 0.0,
                          f"No breakout: close {close:.2f} <= Donchian high {donch_high:.2f}",
                          STRATEGY_TIMEFRAME, adx=adx_val, atr_pct=atr_pct)

        # ── ALL CONDITIONS MET — ENTRY SIGNAL ──
        # Confidence based on ADX strength (stronger trend = higher confidence)
        # ADX 20-25: base confidence, 25-35: good, 35+: strong
        if adx_val >= 35:
            conf = 0.82
        elif adx_val >= 30:
            conf = 0.74
        elif adx_val >= 25:
            conf = 0.66
        else:
            conf = 0.58

        # DI separation boost — wider gap = stronger directional move
        di_gap = plus_di - minus_di
        if di_gap > 15:
            conf = min(0.88, conf + 0.06)
        elif di_gap > 10:
            conf = min(0.85, conf + 0.03)

        reason = (
            f"Donchian breakout: close {close:.2f} > channel high {donch_high:.2f}, "
            f"ADX={adx_val:.1f}, +DI={plus_di:.1f}, -DI={minus_di:.1f}, "
            f"ATR%={atr_pct:.2f}"
        )

        return Signal(
            self.name, "buy", conf, reason, STRATEGY_TIMEFRAME,
            adx=adx_val, atr_pct=atr_pct
        )

    def check_exit(self, features):
        """Check if ADX has dropped below exit threshold (trend dying).
        Returns (should_exit, reason) tuple."""
        if not features.adx_14:
            return False, ""
        adx_val = features.adx_14[-1]
        if adx_val < config.ADX_EXIT_THRESHOLD:
            return True, f"ADX dropped to {adx_val:.1f} (below exit threshold {config.ADX_EXIT_THRESHOLD})"
        return False, ""


# Single strategy instance
STRATEGY = AdaptiveBreakoutStrategy()

# Keep backward-compatible list for any code that iterates STRATEGIES
STRATEGIES = [STRATEGY]


class IntradaySignalEngine:
    def __init__(self, data_provider=None):
        self.data = data_provider or MarketDataProvider()
        self.strategy = STRATEGY

    def evaluate_symbol(self, symbol):
        """Evaluate a single symbol for entry/exit on the 4h timeframe."""
        from alpaca_client import is_equity_symbol
        is_stock = is_equity_symbol(symbol)

        candles = self.data.get_candles(symbol, STRATEGY_TIMEFRAME)
        if len(candles) < 40:
            return self._reject(symbol, f"Insufficient {STRATEGY_TIMEFRAME} candles ({len(candles)})")

        features = FeatureSet(candles)

        # Check for exit signal (ADX drop)
        should_exit, exit_reason = self.strategy.check_exit(features)

        # Check for entry signal
        signal = self.strategy.evaluate(features, is_stock=is_stock)

        adx_val = features.adx_14[-1] if features.adx_14 else 0
        plus_di = features.plus_di_14[-1] if features.plus_di_14 else 0
        minus_di = features.minus_di_14[-1] if features.minus_di_14 else 0

        regime = Regime(
            label="trending" if adx_val >= config.ADX_ENTRY_THRESHOLD else "ranging",
            adx=round(adx_val, 2),
            plus_di=round(plus_di, 2),
            minus_di=round(minus_di, 2),
            atr_pct=round(features.atr_pct, 3),
            reason=signal.reason,
        )

        accepted = signal.action == "buy" and signal.confidence >= 0.38
        # If ADX exit is triggered, override action to sell
        if should_exit:
            signal = Signal(
                self.strategy.name, "sell", 0.7, exit_reason,
                STRATEGY_TIMEFRAME, adx=adx_val, atr_pct=features.atr_pct
            )
            accepted = True

        result = {
            "symbol": symbol,
            "accepted": accepted,
            "action": signal.action,
            "confidence": round(signal.confidence, 3),
            "reason": signal.reason,
            "strategy_signals": [asdict(signal)],
            "trade_regime": asdict(regime),
            "setup_regime": asdict(regime),
            "confirm_regime": asdict(regime),
            "confirm_regimes": {"4h": asdict(regime)},
            "features": {
                "atr_pct_4h": round(features.atr_pct, 3),
                "adx_14": round(adx_val, 2),
                "plus_di_14": round(plus_di, 2),
                "minus_di_14": round(minus_di, 2),
                "volume_ratio_4h": round(features.volume_ratio, 3),
                "donchian_high_20": round(features.donchian_high_20[-1], 4) if features.donchian_high_20 else 0,
                "donchian_low_20": round(features.donchian_low_20[-1], 4) if features.donchian_low_20 else 0,
                "rsi_14": round(features.rsi14[-1], 2) if features.rsi14 else 50,
                # Backward compat key for alpaca_trader
                "atr_pct_15m": round(features.atr_pct, 3),
            },
            "adx_exit": should_exit,
            "adx_exit_reason": exit_reason,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_last(result)
        return result

    def _reject(self, symbol, reason):
        result = {
            "symbol": symbol,
            "accepted": False,
            "action": "hold",
            "confidence": 0.0,
            "reason": reason,
            "strategy_signals": [],
            "trade_regime": {},
            "setup_regime": {},
            "confirm_regime": {},
            "confirm_regimes": {},
            "features": {},
            "adx_exit": False,
            "adx_exit_reason": "",
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
