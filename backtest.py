"""
Honest strategy backtester.

Reuses the LIVE strategy code (strategies.professional_strategies) and the LIVE
data pipeline (intraday_engine.MarketDataProvider), so results reflect what the
bot actually does — not a reimplementation.

Method (per symbol, per strategy):
  * Walk the historical bars with a rolling window (default 250 bars) so every
    indicator, including EMA200, is valid at each step — exactly the shape of
    data the live bot sees.
  * When a strategy returns action == "buy", open ONE simulated long at that
    bar's close using the strategy's OWN recommended stop-loss and take-profit
    (ATR-based, so they scale with the timeframe).
  * Manage forward bar-by-bar: exit at the stop, the target, or a max-hold cap.
    If a single bar spans both stop and target, assume the STOP filled first
    (pessimistic / honest).
  * One open position per strategy at a time (like the live desk).
  * Net of a per-side fee/slippage estimate (default 1 bp, matching the system).

Read-only: fetches historical candles and computes. Places no orders, writes no
state. Safe to run alongside live trading.
"""

import statistics

import config
from intraday_engine import MarketDataProvider
from strategies.professional_strategies import build_feature_context, create_strategy


def _safe(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _metrics(returns, holds):
    """returns: list of net fractional per-trade returns (0.01 = +1%)."""
    n = len(returns)
    if n == 0:
        return {"trades": 0}
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    gross_win = sum(wins)
    gross_loss = sum(losses)  # <= 0
    # max consecutive losses
    cur = mx = 0
    for r in returns:
        if r <= 0:
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    pf = (gross_win / abs(gross_loss)) if gross_loss < 0 else (float("inf") if gross_win > 0 else 0.0)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "avg_win_pct": round(statistics.mean(wins) * 100, 3) if wins else 0.0,
        "avg_loss_pct": round(statistics.mean(losses) * 100, 3) if losses else 0.0,
        "profit_factor": round(pf, 2) if pf != float("inf") else 999.0,
        "expectancy_pct": round(statistics.mean(returns) * 100, 3),
        "total_return_pct": round(sum(returns) * 100, 2),
        "max_consec_losses": mx,
        "avg_hold_bars": round(statistics.mean(holds), 1) if holds else 0.0,
    }


def run_symbol_backtest(symbol, timeframe="1D", limit=1500, window=250,
                        max_hold_bars=60, fee_bps=1.0, data_provider=None,
                        strategies=None):
    provider = data_provider or MarketDataProvider()
    candles = provider.get_candles(symbol, timeframe, limit=limit) or []
    n = len(candles)
    if n < window + 30:
        return {"symbol": symbol, "timeframe": timeframe, "bars": n,
                "error": f"insufficient data ({n} bars, need >= {window + 30})",
                "per_strategy": {}}

    highs = [_safe(c.get("high")) for c in candles]
    lows = [_safe(c.get("low")) for c in candles]
    closes = [_safe(c.get("close")) for c in candles]

    strat_names = strategies or list(config.PROFESSIONAL_STRATEGIES)
    strat_objs = {}
    for name in strat_names:
        try:
            strat_objs[name] = create_strategy(name)
        except Exception:
            continue

    # entries[name] = {bar_index: (stop, take)}
    entries = {name: {} for name in strat_objs}
    fee = fee_bps / 10000.0

    # 1) Build features once per bar, collect each strategy's buy signals.
    for i in range(window - 1, n):
        win = candles[i - window + 1:i + 1]
        try:
            fc = build_feature_context(win)
        except Exception:
            continue
        for name, strat in strat_objs.items():
            try:
                sig = strat.evaluate(fc)
            except Exception:
                continue
            if getattr(sig, "action", "") != "buy":
                continue
            stop = _safe(sig.recommended_stop_loss)
            take = _safe(sig.recommended_take_profit)
            entry_px = closes[i]
            if stop <= 0 or take <= 0 or entry_px <= 0 or stop >= entry_px or take <= entry_px:
                continue
            entries[name][i] = (stop, take)

    # 2) Simulate one-position-at-a-time per strategy.
    per_strategy = {}
    for name in strat_objs:
        returns, holds, exit_kinds = [], [], {"stop": 0, "target": 0, "max_hold": 0}
        in_trade = False
        entry_i = entry_px = stop = take = 0
        for i in range(window - 1, n):
            if in_trade:
                exited = False
                if lows[i] <= stop:          # pessimistic: stop before target
                    exit_px, kind = stop, "stop"; exited = True
                elif highs[i] >= take:
                    exit_px, kind = take, "target"; exited = True
                elif (i - entry_i) >= max_hold_bars:
                    exit_px, kind = closes[i], "max_hold"; exited = True
                if exited:
                    gross = exit_px / entry_px - 1.0
                    net = gross - 2 * fee   # entry side + exit side
                    returns.append(net)
                    holds.append(i - entry_i)
                    exit_kinds[kind] += 1
                    in_trade = False
            if not in_trade and i in entries[name]:
                stop, take = entries[name][i]
                entry_px = closes[i]
                entry_i = i
                in_trade = True
        m = _metrics(returns, holds)
        m["exit_kinds"] = exit_kinds
        per_strategy[name] = m

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": n,
        "date_start": candles[0].get("timestamp") or candles[0].get("time"),
        "date_end": candles[-1].get("timestamp") or candles[-1].get("time"),
        "window": window,
        "max_hold_bars": max_hold_bars,
        "fee_bps_per_side": fee_bps,
        "per_strategy": per_strategy,
    }
