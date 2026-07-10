"""
Market-trend filter — "don't fight the tape".

A long-only momentum desk should not open new longs while the broad market
is in a downtrend.  This module checks SPY's daily trend (price vs its moving
average) once per cycle and returns whether NEW long entries are allowed.

Design notes:
  * Only NEW entries are gated.  Existing positions are untouched — they are
    still managed by the exit manager (stops, targets, trailing, EOD).
  * Fail-OPEN: if SPY data can't be loaded, longs are allowed and the reason
    is logged.  A transient data hiccup must never silently halt the whole
    desk (that would be worse than the occasional bad-tape trade).
  * Fully tunable / reversible via config + env (MARKET_FILTER_ENABLED=0).
"""

import logging

import config

logger = logging.getLogger("market_filter")


def _sma(values, period):
    if not values or len(values) < period:
        return None
    return sum(values[-period:]) / period


def market_trend_status(data_provider):
    """Return a dict describing whether new longs are allowed right now.

    Keys: enabled, allow_longs, reason, symbol, close, ma_fast, ma_slow, mode,
          degraded (True when data was insufficient/errored and we failed open).
    """
    enabled = getattr(config, "MARKET_FILTER_ENABLED", True)
    if not enabled:
        return {"enabled": False, "allow_longs": True,
                "reason": "Market filter disabled — longs allowed."}

    symbol = getattr(config, "MARKET_FILTER_SYMBOL", "SPY")
    fast = int(getattr(config, "MARKET_FILTER_MA_PERIOD", 50))
    mode = getattr(config, "MARKET_FILTER_MODE", "50ma")

    try:
        candles = data_provider.get_candles(symbol, "1D", limit=max(fast, 200) + 20)
        closes = [float(c.get("close")) for c in (candles or []) if c.get("close") is not None]
        if len(closes) < fast + 1:
            return {"enabled": True, "allow_longs": True, "degraded": True,
                    "symbol": symbol,
                    "reason": (f"Insufficient {symbol} daily data "
                               f"({len(closes)} bars) — filter open, longs allowed.")}

        close = closes[-1]
        ma_fast = _sma(closes, fast)
        ma_slow = _sma(closes, 200)
        above_fast = close >= ma_fast
        above_slow = (ma_slow is None) or (close >= ma_slow)

        if mode == "200ma":
            allow = above_slow
        elif mode == "50and200":
            allow = above_fast and above_slow
        else:  # "50ma" (default)
            allow = above_fast

        reason = (
            f"{symbol} {close:.2f} "
            f"{'>=' if above_fast else '<'} {fast}D SMA {ma_fast:.2f}"
            + (f", {'>=' if above_slow else '<'} 200D SMA {ma_slow:.2f}" if ma_slow else "")
            + (". Uptrend — new longs allowed." if allow
               else ". Market downtrend — new longs blocked (existing positions still managed).")
        )
        return {"enabled": True, "allow_longs": bool(allow), "reason": reason,
                "symbol": symbol, "mode": mode,
                "close": round(close, 2), "ma_fast": round(ma_fast, 2),
                "ma_slow": round(ma_slow, 2) if ma_slow else None}
    except Exception as e:  # fail open
        logger.warning("market filter data error (%s) — allowing longs", e)
        return {"enabled": True, "allow_longs": True, "degraded": True,
                "symbol": symbol,
                "reason": f"Filter data error ({e}) — filter open, longs allowed."}
