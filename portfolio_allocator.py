"""
Strategy Factory Bot Manager — Portfolio Allocator
Takes a starting capital (e.g. $1,000) and intelligently distributes it
across active strategies based on quantum scores, risk metrics, and diversification.
"""

ACTIVE_COINS = {"BTC", "ETH", "SOL", "XRP", "LINK", "AVAX", "DOGE", "ADA", "DOT", "UNI", "AAVE", "LTC"}
ACTIVE_STRATEGIES = {
    "grid", "mean_reversion", "momentum", "trend_following", "breakout",
    "pullback_continuation", "volatility_breakout", "range_trading",
    "swing_trading", "reversal_market_structure", "grid_range",
}


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def allocate_portfolio(capital, evaluations, min_allocation_pct=0.3, max_allocation_pct=25.0):
    """
    Allocate capital across eligible strategies using score-weighted sizing.

    Args:
        capital: Total starting capital (e.g. 100000)
        evaluations: List of evaluation dicts from daily_runner
        min_allocation_pct: Reserved for future dust-position filtering.
        max_allocation_pct: Reserved for future concentration filtering.

    Returns:
        dict with:
            - allocations: list of {bot_name, pair, allocation_usd, allocation_pct, reasoning}
            - summary: portfolio-level stats
            - excluded: strategies that were skipped and why
    """
    eligible = []
    excluded = []

    for ev in evaluations:
        verdict = ev.get("enhanced_verdict", ev.get("verdict", "HOLD")).upper()
        m = ev.get("metrics", {})
        adapt = ev.get("adaptation_score", 50)
        real_score = ev.get("real_paper_score")
        real_closed = int(ev.get("real_paper_closed_trades", 0) or 0)
        real_avg_pl = _safe_float(ev.get("real_paper_avg_pl_pct"), None)
        strategy_disabled = bool(ev.get("strategy_disabled"))

        # Skip paused, insufficient data, or strategies with terrible metrics
        if strategy_disabled:
            excluded.append({"bot_name": ev.get("bot_name"), "reason": "DISABLED — repeated live damage or risk flag"})
            continue
        if verdict == "PAUSE":
            excluded.append({"bot_name": ev.get("bot_name"), "reason": "PAUSED — strategy flagged for poor performance"})
            continue
        if "INSUFFICIENT" in verdict:
            excluded.append({"bot_name": ev.get("bot_name"), "reason": "INSUFFICIENT DATA — not enough trades to trust"})
            continue

        win_rate = m.get("win_rate", 0)
        pf = m.get("profit_factor", 0)
        sharpe = m.get("sharpe_ratio", 0)
        dd = abs(m.get("max_drawdown", 0))

        coin = ev.get("pair", "").split("/")[0].upper()
        stype = ev.get("strategy_type", "").lower()
        if coin not in ACTIVE_COINS:
            excluded.append({"bot_name": ev.get("bot_name"), "reason": f"Coin {coin} not in active set"})
            continue
        if stype not in ACTIVE_STRATEGIES:
            excluded.append({"bot_name": ev.get("bot_name"), "reason": f"Strategy type {stype} not in active set"})
            continue

        if pf <= 0 or win_rate <= 0:
            excluded.append({"bot_name": ev.get("bot_name"), "reason": "Zero or negative profit factor"})
            continue

        if pf < 1.1 or win_rate < 45 or sharpe < 0.2:
            excluded.append({"bot_name": ev.get("bot_name"), "reason": f"Below quality threshold (WR={win_rate:.0f}%, PF={pf:.2f}, Sharpe={sharpe:.2f})"})
            continue
        if real_closed >= 5 and real_score is not None and float(real_score) < 35:
            excluded.append({"bot_name": ev.get("bot_name"), "reason": f"Weak real paper evidence (score {float(real_score):.0f} on {real_closed} closes)"})
            continue

        # Compute a composite score for ranking and allocation weight.
        score = 0
        score += min(25, max(0, (win_rate - 40) * 0.625))
        score += min(25, max(0, (pf - 0.8) * 17.86))
        score += min(20, max(0, sharpe * 13.33))
        score += min(15, max(0, (20 - dd) * 0.75))
        score += min(15, max(0, (adapt - 30) * 0.214))
        if real_score is not None and real_closed > 0:
            trust = min(1.0, real_closed / 10.0)
            score += (float(real_score) - 50.0) * 0.42 * trust
        if real_closed >= 3 and real_avg_pl is not None:
            score += max(-10, min(10, float(real_avg_pl) * 2.4))
        if dd > 20:
            score *= 0.7
        elif dd > 15:
            score *= 0.85

        eligible.append({
            "bot_name": ev.get("bot_name", "?"),
            "pair": ev.get("pair", ""),
            "strategy_type": ev.get("strategy_type", ""),
            "score": max(1, score),
            "metrics": m,
            "adaptation_score": adapt,
            "verdict": verdict,
            "real_paper_score": real_score,
            "real_paper_closed_trades": real_closed,
            "real_paper_avg_pl_pct": real_avg_pl,
        })

    coin_counts = {}
    for e in eligible:
        coin = e["pair"].split("/")[0].upper()
        seen = coin_counts.get(coin, 0)
        if seen > 0:
            e["score"] *= max(0.55, 1.0 - (0.18 * seen))
        coin_counts[coin] = seen + 1

    if not eligible:
        return {
            "allocations": [],
            "excluded": excluded,
            "summary": {
                "total_capital": capital,
                "allocated": 0,
                "reserve": capital,
                "num_strategies": 0,
                "message": "No strategies eligible for allocation. All are paused or have insufficient data."
            }
        }

    # ── Score-weighted allocation ──────────────────────────────────────
    total_score = sum(e["score"] for e in eligible)
    if total_score <= 0:
        total_score = len(eligible)  # fallback
    for e in eligible:
        raw_pct = (e["score"] / total_score) * 100.0
        # Keep a tighter upstream cap so multiple bots cannot dominate before
        # the execution layer applies its own symbol exposure limits.
        e["final_pct"] = min(raw_pct, 10.0)

    # Re-normalize after capping
    capped_total = sum(e["final_pct"] for e in eligible)
    if capped_total > 0:
        for e in eligible:
            e["final_pct"] = (e["final_pct"] / capped_total) * 100.0
            e["allocation_usd"] = round(capital * e["final_pct"] / 100, 2)

    # Sort by score (highest first) for display ranking
    eligible.sort(key=lambda x: x["score"], reverse=True)

    # Build allocations list with reasoning
    allocations = []
    for e in eligible:
        m = e["metrics"]
        reasons = []
        if m.get("win_rate", 0) >= 55:
            reasons.append(f"Strong win rate ({m['win_rate']:.1f}%)")
        elif m.get("win_rate", 0) >= 48:
            reasons.append(f"Decent win rate ({m['win_rate']:.1f}%)")
        else:
            reasons.append(f"Lower win rate ({m['win_rate']:.1f}%) but profitable")

        if m.get("profit_factor", 0) >= 1.5:
            reasons.append(f"Excellent profit factor ({m['profit_factor']:.2f})")
        elif m.get("profit_factor", 0) >= 1.1:
            reasons.append(f"Good profit factor ({m['profit_factor']:.2f})")

        if m.get("sharpe_ratio", 0) >= 0.8:
            reasons.append("Strong risk-adjusted returns")
        if abs(m.get("max_drawdown", 0)) < 10:
            reasons.append("Low drawdown risk")
        if e.get("real_paper_closed_trades", 0) >= 3 and e.get("real_paper_score") is not None:
            reasons.append(f"Real paper score {float(e['real_paper_score']):.0f} on {e['real_paper_closed_trades']} closes")

        allocations.append({
            "bot_name": e["bot_name"],
            "pair": e["pair"],
            "strategy_type": e["strategy_type"],
            "allocation_usd": e["allocation_usd"],
            "allocation_pct": round(e["final_pct"], 1),
            "score": round(e["score"], 1),
            "reasoning": " · ".join(reasons) if reasons else "Meets minimum criteria",
            "win_rate": m.get("win_rate", 0),
            "profit_factor": m.get("profit_factor", 0),
            "sharpe_ratio": m.get("sharpe_ratio", 0),
            "max_drawdown": m.get("max_drawdown", 0),
            "real_paper_score": e.get("real_paper_score"),
            "real_paper_closed_trades": e.get("real_paper_closed_trades", 0),
            "expected_monthly_return": _estimate_monthly_return(
                m,
                e["allocation_usd"],
                real_paper_avg_pl_pct=e.get("real_paper_avg_pl_pct"),
                real_paper_closed_trades=e.get("real_paper_closed_trades", 0),
            ),
        })

    total_allocated = sum(a["allocation_usd"] for a in allocations)
    total_expected = sum(a["expected_monthly_return"] for a in allocations)

    # Diversification score (0-100): how spread out is the portfolio?
    weights = [a["allocation_pct"] / 100 for a in allocations]
    herfindahl = sum(w**2 for w in weights)
    diversification = round((1 - herfindahl) * 100, 1)

    # Strategy type distribution
    type_dist = {}
    for a in allocations:
        t = a["strategy_type"]
        type_dist[t] = type_dist.get(t, 0) + a["allocation_pct"]

    summary = {
        "total_capital": capital,
        "allocated": round(total_allocated, 2),
        "reserve": round(capital - total_allocated, 2),
        "num_strategies": len(allocations),
        "num_excluded": len(excluded),
        "diversification_score": diversification,
        "expected_monthly_return_usd": round(total_expected, 2),
        "expected_monthly_return_pct": round(total_expected / capital * 100, 2) if capital > 0 else 0,
        "strategy_type_distribution": type_dist,
    }

    return {
        "allocations": allocations,
        "excluded": excluded,
        "summary": summary,
    }


def _estimate_monthly_return(metrics, allocation, real_paper_avg_pl_pct=None, real_paper_closed_trades=0):
    """Rough monthly return estimate based on historical metrics."""
    wr = metrics.get("win_rate", 50) / 100
    avg_win = metrics.get("avg_win", 10)
    avg_loss = metrics.get("avg_loss", 10)
    trades_per_day = metrics.get("total_trades", 100) / 30  # rough daily

    # Expected value per trade
    ev_per_trade = (wr * avg_win) - ((1 - wr) * avg_loss)

    # Scale to allocation size (normalize by typical trade notional)
    if avg_win + avg_loss > 0:
        scale_factor = allocation / (avg_win * 20)  # rough scaling
    else:
        scale_factor = 1

    monthly_trades = trades_per_day * 30
    estimated_return = ev_per_trade * monthly_trades * min(scale_factor, 2) * 0.01  # conservative

    if real_paper_closed_trades >= 3 and real_paper_avg_pl_pct is not None:
        evidence = min(1.0, real_paper_closed_trades / 10.0)
        if real_paper_avg_pl_pct < 0:
            estimated_return *= max(0.2, 1.0 - (abs(real_paper_avg_pl_pct) * 0.12 * evidence))
        else:
            estimated_return *= min(1.35, 1.0 + (real_paper_avg_pl_pct * 0.04 * evidence))

    return round(estimated_return, 2)
