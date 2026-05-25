"""
Real Backtest Engine — Fetches actual price data from Alpaca (US-friendly).
Falls back to Binance.US and KuCoin if needed.
Run on YOUR machine. No fake data.

Usage:
    # Option 1: Set Alpaca keys (recommended — you already have them on Railway)
    export ALPACA_API_KEY=your-key
    export ALPACA_API_SECRET=your-secret
    python3 real_backtest.py

    # Option 2: Without keys (uses Binance.US or KuCoin public API)
    python3 real_backtest.py

Results saved to: real_backtest_results.json + printed to console
"""

import requests
import json
import time
import math
import os
from datetime import datetime, timedelta

# ── Configuration ─────────────────────────────────────────────────
COINS = ["BTC", "ETH", "SOL", "XRP", "LINK", "AVAX", "ADA", "UNI", "AAVE", "LTC"]

STRATEGIES = [
    {"fast": 12, "slow": 26, "name": "EMA(12/26)"},
    {"fast": 9,  "slow": 21, "name": "EMA(9/21)"},
    {"fast": 8,  "slow": 21, "name": "EMA(8/21)"},
    {"fast": 5,  "slow": 13, "name": "EMA(5/13)"},
    {"fast": 20, "slow": 50, "name": "EMA(20/50)"},
]

RISK_CONFIGS = [
    {"sl_atr": 1.0, "tp_atr": 2.0, "label": "1:2"},
    {"sl_atr": 1.5, "tp_atr": 3.0, "label": "1.5:3"},
    {"sl_atr": 1.0, "tp_atr": 3.0, "label": "1:3"},
    {"sl_atr": 2.0, "tp_atr": 4.0, "label": "2:4"},
]

TIMEFRAMES = [
    {"interval": "15m", "days": 90,  "label": "15m"},
    {"interval": "1h",  "days": 180, "label": "1h"},
    {"interval": "4h",  "days": 365, "label": "4h"},
]

COMMISSION = 0.075  # percent per side
CAPITAL = 10000.0

# Alpaca keys (same ones you have on Railway)
ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_API_SECRET", "")


# ── Data Fetching (multi-provider, US-friendly) ───────────────────

# Alpaca timeframe mapping
_ALPACA_TF = {"15m": "15Min", "1h": "1Hour", "4h": "4Hour", "1d": "1Day"}


def _fetch_alpaca(symbol, interval, days):
    """Fetch from Alpaca crypto data API (free, US-based)."""
    if not ALPACA_KEY or not ALPACA_SECRET:
        return []
    alpaca_sym = f"{symbol}/USD"
    tf = _ALPACA_TF.get(interval)
    if not tf:
        return []

    start = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    end = datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")
    url = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    candles = []
    page_token = None

    while True:
        params = {"symbols": alpaca_sym, "timeframe": tf, "start": start, "end": end, "limit": 10000}
        if page_token:
            params["page_token"] = page_token
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[Alpaca] {e}")
            break

        bars = data.get("bars", {}).get(alpaca_sym, [])
        for b in bars:
            candles.append({
                "t": int(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).timestamp() * 1000),
                "o": float(b["o"]), "h": float(b["h"]),
                "l": float(b["l"]), "c": float(b["c"]), "v": float(b["v"])
            })

        page_token = data.get("next_page_token")
        if not page_token:
            break
        time.sleep(0.15)

    return candles


def _fetch_kucoin(symbol, interval, days):
    """Fetch from KuCoin public API (no auth, US-accessible)."""
    kucoin_sym = f"{symbol}-USDT"
    tf_map = {"15m": "15min", "1h": "1hour", "4h": "4hour", "1d": "1day"}
    kc_tf = tf_map.get(interval)
    if not kc_tf:
        return []

    end_ts = int(time.time())
    start_ts = end_ts - (days * 86400)
    url = "https://api.kucoin.com/api/v1/market/candles"
    candles = []

    try:
        r = requests.get(url, params={
            "symbol": kucoin_sym, "type": kc_tf,
            "startAt": start_ts, "endAt": end_ts
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("code") == "200000" and data.get("data"):
            for k in data["data"]:
                # KuCoin: [time, open, close, high, low, volume, turnover]
                candles.append({
                    "t": int(k[0]) * 1000,
                    "o": float(k[1]), "h": float(k[3]),
                    "l": float(k[4]), "c": float(k[2]), "v": float(k[5])
                })
            candles.sort(key=lambda x: x["t"])
    except Exception as e:
        print(f"[KuCoin] {e}")

    return candles


def _fetch_binance_us(symbol, interval, days):
    """Fetch from Binance.US (US-accessible alternative to Binance.com)."""
    pair = f"{symbol}USDT" if symbol != "UNI" else f"{symbol}USD"
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)
    candles = []
    cur = start_ms

    while cur < end_ms:
        url = "https://api.binance.us/api/v3/klines"
        params = {"symbol": pair, "interval": interval, "startTime": cur, "endTime": end_ms, "limit": 1000}
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"[Binance.US] {e}")
            break
        if not data:
            break
        for k in data:
            candles.append({
                "t": k[0], "o": float(k[1]), "h": float(k[2]),
                "l": float(k[3]), "c": float(k[4]), "v": float(k[5])
            })
        cur = data[-1][0] + 1
        if len(data) < 1000:
            break
        time.sleep(0.1)

    return candles


def fetch_candles(symbol, interval, days):
    """Try Alpaca → KuCoin → Binance.US in order."""
    # Provider 1: Alpaca (best for US, you have the keys)
    candles = _fetch_alpaca(symbol, interval, days)
    if len(candles) >= 100:
        return candles

    # Provider 2: KuCoin (public, no auth, 1500 candle limit)
    candles = _fetch_kucoin(symbol, interval, days)
    if len(candles) >= 100:
        return candles

    # Provider 3: Binance.US
    candles = _fetch_binance_us(symbol, interval, days)
    if len(candles) >= 100:
        return candles

    return candles

    return candles


# ── Indicators ────────────────────────────────────────────────────
def ema(values, period):
    result = [values[0]]
    k = 2.0 / (period + 1)
    for i in range(1, len(values)):
        result.append(values[i] * k + result[-1] * (1 - k))
    return result


def atr(candles, period=14):
    trs = []
    for i in range(len(candles)):
        h, l, c_prev = candles[i]["h"], candles[i]["l"], candles[i - 1]["c"] if i > 0 else candles[i]["c"]
        trs.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    # SMA of TR
    result = [None] * (period - 1)
    for i in range(period - 1, len(trs)):
        result.append(sum(trs[i - period + 1:i + 1]) / period)
    return result


def rsi(closes, period=14):
    result = [None] * period
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0, d))
        losses.append(max(0, -d))

    if len(gains) < period:
        return [None] * len(closes)

    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    if avg_l == 0:
        result.append(100.0)
    else:
        result.append(100.0 - (100.0 / (1.0 + avg_g / avg_l)))

    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
        if avg_l == 0:
            result.append(100.0)
        else:
            result.append(100.0 - (100.0 / (1.0 + avg_g / avg_l)))

    return result


# ── Backtest Engine ───────────────────────────────────────────────
def backtest(candles, fast_p, slow_p, sl_mult, tp_mult, use_rsi=True, trend_filter=False, long_only=True):
    closes = [c["c"] for c in candles]
    ema_f = ema(closes, fast_p)
    ema_s = ema(closes, slow_p)
    ema_20 = ema(closes, 20)
    ema_50 = ema(closes, 50)
    atr_vals = atr(candles, 14)
    rsi_vals = rsi(closes, 14)

    start = max(slow_p, 50) + 2
    if len(candles) < start + 10:
        return {"total_trades": 0, "win_rate": 0, "profit_factor": 0, "total_return_pct": 0,
                "max_drawdown_pct": 0, "sharpe": 0, "final_equity": CAPITAL}

    equity = CAPITAL
    peak = CAPITAL
    max_dd = 0
    pos = None
    trades = []

    for i in range(start, len(candles)):
        c = candles[i]
        price = c["c"]
        high = c["h"]
        low = c["l"]
        a = atr_vals[i]

        if a is None or a <= 0:
            continue

        # Check exit
        if pos:
            exited = False
            exit_p = None
            reason = None

            if pos["side"] == "long":
                if low <= pos["sl"]:
                    exit_p, reason, exited = pos["sl"], "SL", True
                elif high >= pos["tp"]:
                    exit_p, reason, exited = pos["tp"], "TP", True
            else:
                if high >= pos["sl"]:
                    exit_p, reason, exited = pos["sl"], "SL", True
                elif low <= pos["tp"]:
                    exit_p, reason, exited = pos["tp"], "TP", True

            if exited:
                if pos["side"] == "long":
                    pnl = (exit_p - pos["entry"]) / pos["entry"] * 100
                else:
                    pnl = (pos["entry"] - exit_p) / pos["entry"] * 100
                pnl -= COMMISSION * 2
                equity += equity * pnl / 100
                trades.append({"pnl": pnl, "reason": reason, "side": pos["side"]})
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
                pos = None

        # Check entry
        if pos is None:
            bull = ema_f[i - 1] <= ema_s[i - 1] and ema_f[i] > ema_s[i]
            bear = ema_f[i - 1] >= ema_s[i - 1] and ema_f[i] < ema_s[i]

            atr_pct = (a / price) * 100
            if atr_pct < 0.3:
                bull = bear = False

            if use_rsi and rsi_vals[i] is not None:
                if bull and rsi_vals[i] > 75:
                    bull = False
                if bear and rsi_vals[i] < 25:
                    bear = False

            if trend_filter:
                if bull and ema_20[i] < ema_50[i]:
                    bull = False
                if bear and ema_20[i] > ema_50[i]:
                    bear = False

            if bull:
                pos = {"side": "long", "entry": price,
                       "sl": price - a * sl_mult, "tp": price + a * tp_mult}
            elif bear and not long_only:
                pos = {"side": "short", "entry": price,
                       "sl": price + a * sl_mult, "tp": price - a * tp_mult}

    # Close open position
    if pos:
        last = candles[-1]["c"]
        if pos["side"] == "long":
            pnl = (last - pos["entry"]) / pos["entry"] * 100
        else:
            pnl = (pos["entry"] - last) / pos["entry"] * 100
        pnl -= COMMISSION * 2
        equity += equity * pnl / 100
        trades.append({"pnl": pnl, "reason": "OPEN", "side": pos["side"]})

    if not trades:
        return {"total_trades": 0, "win_rate": 0, "profit_factor": 0, "total_return_pct": 0,
                "max_drawdown_pct": 0, "sharpe": 0, "final_equity": CAPITAL}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_win = sum(t["pnl"] for t in wins)
    total_loss = abs(sum(t["pnl"] for t in losses))
    rets = [t["pnl"] for t in trades]
    avg = sum(rets) / len(rets)
    std = (sum((r - avg) ** 2 for r in rets) / len(rets)) ** 0.5 if len(rets) > 1 else 0

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "profit_factor": round(total_win / total_loss, 2) if total_loss > 0 else 99,
        "total_return_pct": round((equity - CAPITAL) / CAPITAL * 100, 2),
        "final_equity": round(equity, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round((avg / std) * (len(trades) ** 0.5), 2) if std > 0 else 0,
        "avg_win_pct": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
        "avg_loss_pct": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0,
        "tp_exits": sum(1 for t in trades if t["reason"] == "TP"),
        "sl_exits": sum(1 for t in trades if t["reason"] == "SL"),
    }


# ── Main ──────────────────────────────────────────────────────────
def main():
    print("=" * 75)
    print("  REAL BACKTEST — Actual Binance OHLCV Data")
    print("  Testing 5 EMA variants × 4 risk configs × 3 timeframes × 10 coins")
    print("  Long-only + Long/Short × With/Without trend filter")
    print("=" * 75)

    all_results = []
    cache = {}

    for tf in TIMEFRAMES:
        print(f"\n{'─' * 75}")
        print(f"  TIMEFRAME: {tf['label']} ({tf['days']} days)")
        print(f"{'─' * 75}")

        for coin in COINS:
            cache_key = f"{coin}_{tf['interval']}"
            if cache_key not in cache:
                print(f"  Fetching {coin}/USDT {tf['label']}...", end=" ", flush=True)
                candles = fetch_candles(coin, tf["interval"], tf["days"])
                if len(candles) < 100:
                    print(f"SKIP ({len(candles)} candles)")
                    continue
                print(f"{len(candles)} candles")
                cache[cache_key] = candles
            candles = cache[cache_key]

            for strat in STRATEGIES:
                for risk in RISK_CONFIGS:
                    for long_only in [True, False]:
                        for trend_f in [False, True]:
                            stats = backtest(candles, strat["fast"], strat["slow"],
                                             risk["sl_atr"], risk["tp_atr"],
                                             use_rsi=True, trend_filter=trend_f,
                                             long_only=long_only)
                            all_results.append({
                                "coin": coin, "tf": tf["label"],
                                "strategy": strat["name"],
                                "fast": strat["fast"], "slow": strat["slow"],
                                "risk": risk["label"],
                                "sl": risk["sl_atr"], "tp": risk["tp_atr"],
                                "long_only": long_only, "trend_filter": trend_f,
                                **stats,
                            })

    # ── Report ────────────────────────────────────────────────────
    valid = [r for r in all_results if r["total_trades"] >= 5]
    profitable = sorted([r for r in valid if r["profit_factor"] > 1.0],
                        key=lambda x: x["total_return_pct"], reverse=True)

    print("\n" + "=" * 75)
    print("  TOP 25 PROFITABLE CONFIGURATIONS (≥5 trades, PF > 1.0)")
    print("=" * 75)

    if not profitable:
        print("\n  *** NO PROFITABLE CONFIGURATIONS FOUND ***")
        print("  Showing least-bad:")
        least_bad = sorted(valid, key=lambda x: x["total_return_pct"], reverse=True)[:15]
        for r in least_bad:
            lo = "L" if r["long_only"] else "L+S"
            tf = "+TF" if r["trend_filter"] else ""
            print(f"  {r['coin']:5s} {r['tf']:4s} {r['strategy']:12s} {r['risk']:5s} {lo:3s}{tf:3s} | "
                  f"Trades={r['total_trades']:3d} WR={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f} "
                  f"Ret={r['total_return_pct']:+8.2f}% DD={r['max_drawdown_pct']:5.1f}%")
    else:
        for r in profitable[:25]:
            lo = "L" if r["long_only"] else "L+S"
            tf = "+TF" if r["trend_filter"] else ""
            print(f"  {r['coin']:5s} {r['tf']:4s} {r['strategy']:12s} {r['risk']:5s} {lo:3s}{tf:3s} | "
                  f"Trades={r['total_trades']:3d} WR={r['win_rate']:5.1f}% PF={r['profit_factor']:5.2f} "
                  f"Ret={r['total_return_pct']:+8.2f}% DD={r['max_drawdown_pct']:5.1f}% Sharpe={r['sharpe']:5.2f}")

    # Best per coin
    print(f"\n{'─' * 75}")
    print("  BEST PER COIN")
    print(f"{'─' * 75}")
    for coin in COINS:
        coin_p = [r for r in profitable if r["coin"] == coin]
        if not coin_p:
            coin_all = [r for r in valid if r["coin"] == coin]
            if coin_all:
                best = max(coin_all, key=lambda x: x["total_return_pct"])
                print(f"  {coin:5s}: NO PROFIT — best was {best['strategy']} {best['tf']} {best['risk']} → {best['total_return_pct']:+.2f}%")
            else:
                print(f"  {coin:5s}: NO DATA")
        else:
            b = coin_p[0]
            lo = "L" if b["long_only"] else "L+S"
            tf = "+TF" if b["trend_filter"] else ""
            print(f"  {coin:5s}: {b['strategy']} {b['tf']} {b['risk']} {lo}{tf} → "
                  f"Ret={b['total_return_pct']:+.2f}% WR={b['win_rate']:.1f}% PF={b['profit_factor']:.2f} "
                  f"Trades={b['total_trades']} DD={b['max_drawdown_pct']:.1f}%")

    # Strategy comparison
    print(f"\n{'─' * 75}")
    print("  STRATEGY COMPARISON (avg across all coins & configs)")
    print(f"{'─' * 75}")
    strat_names = sorted(set(r["strategy"] for r in valid))
    for s in strat_names:
        sd = [r for r in valid if r["strategy"] == s]
        n_prof = sum(1 for r in sd if r["profit_factor"] > 1.0)
        avg_ret = sum(r["total_return_pct"] for r in sd) / len(sd)
        avg_pf = sum(r["profit_factor"] for r in sd) / len(sd)
        avg_wr = sum(r["win_rate"] for r in sd) / len(sd)
        print(f"  {s:12s}: AvgRet={avg_ret:+6.2f}% AvgWR={avg_wr:5.1f}% AvgPF={avg_pf:5.2f} "
              f"Profitable={n_prof}/{len(sd)}")

    # Timeframe comparison
    print(f"\n{'─' * 75}")
    print("  TIMEFRAME COMPARISON")
    print(f"{'─' * 75}")
    for tf in TIMEFRAMES:
        td = [r for r in valid if r["tf"] == tf["label"]]
        if td:
            n_prof = sum(1 for r in td if r["profit_factor"] > 1.0)
            avg_ret = sum(r["total_return_pct"] for r in td) / len(td)
            print(f"  {tf['label']:4s}: AvgRet={avg_ret:+6.2f}% Profitable={n_prof}/{len(td)}")

    # Save
    with open("real_backtest_results.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    print(f"\n  Total configs tested: {len(all_results)}")
    print(f"  Valid (≥5 trades):    {len(valid)}")
    print(f"  Profitable (PF>1):   {len(profitable)}")
    print(f"  Results → real_backtest_results.json")
    print("=" * 75)

    return profitable, valid


if __name__ == "__main__":
    profitable, valid = main()
