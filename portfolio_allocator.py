"""
Strategy Factory Bot Manager — Portfolio Allocator
Takes a starting capital (e.g. $1,000) and intelligently distributes it
across active strategies based on quantum scores, risk metrics, and diversification.
"""

import math


def allocate_portfolio(capital, evaluations, min_allocation_pct=3.0, max_allocation_pct=25.0):
    """
    Allocate capital across strategies based on performance and risk.

    Args:
        capital: Total starting capital (e.g. 1000)
        evaluations: List of evaluation dicts from daily_runner
        min_allocation_pct: Minimum % per strategy (avoids dust positions)
        max_allocation_pct: Maximum % per strategy (avoids over-concentration)

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

        # Skip paused, insufficient data, or strategies with terrible metrics
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

        if pf <= 0 or win_rate <= 0:
            excluded.append({"bot_name": ev.get("bot_name"), "reason": "Zero or negative profit factor"})
            continue

        # Compute a composite score for allocation weight
        # Higher = deserves more capital
        score = 0

        # Win rate contribution (0-25 points)
        score += min(25, max(0, (win_rate - 40) * 0.625))

        # Profit factor contribution (0-25 points)
        score += min(25, max(0, (pf - 0.8) * 17.86))

        # Sharpe ratio contribution (0-20 points)
        score += min(20, max(0, sharpe * 13.33))

        # Low drawdown bonus (0-15 points) — less drawdown = more allocation
        score += min(15, max(0, (20 - dd) * 0.75))

        # Adaptation score contribution (0-15 points)
        score += min(15, max(0, (adapt - 30) * 0.214))

        # Risk penalty for very high drawdown
        if dd > 20:
            score *= 0.7
        elif dd > 15:
            score *= 0.85

        eligible.append({
            "bot_name": ev.get("bot_name", "?"),
            "pair": ev.get("pair", ""),
            "strategy_type": ev.get("strategy_type", ""),
            "score": max(1, score),  # floor at 1
            "metrics": m,
            "adaptation_score": adapt,
            "verdict": verdict,
        })

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

    # Normalize scores to get raw weights
    total_score = sum(e["score"] for e in eligible)
    for e in eligible:
        e["raw_weight"] = e["score"] / total_score * 100  # as percentage

    # Apply min/max constraints and re-normalize
    # First pass: clip to bounds
    for e in eligible:
        e["clipped_weight"] = max(min_allocation_pct, min(max_allocation_pct, e["raw_weight"]))

    # Re-normalize to sum to 100%
    total_clipped = sum(e["clipped_weight"] for e in eligible)
    for e in eligible:
        e["final_pct"] = e["clipped_weight"] / total_clipped * 100
        e["allocation_usd"] = round(capital * e["final_pct"] / 100, 2)

    # Sort by allocation (highest first)
    eligible.sort(key=lambda x: x["allocation_usd"], reverse=True)

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
            "expected_monthly_return": _estimate_monthly_return(m, e["allocation_usd"]),
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


def _estimate_monthly_return(metrics, allocation):
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

    return round(estimated_return, 2)
