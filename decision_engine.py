"""
Decision Engine for Crypto Trading Bot Management System

Threshold-based verdict engine supporting four verdicts: PAUSE, HOLD, REACTIVATE, and INSUFFICIENT_DATA.
Evaluates bot metrics against configurable thresholds to generate actionable decisions.
"""

from config import (
    PAUSE_WIN_RATE,
    PAUSE_MAX_DRAWDOWN,
    PAUSE_PROFIT_FACTOR,
    PAUSE_CONSECUTIVE_LOSSES,
    PAUSE_SHARPE_RATIO,
    PAUSE_AVG_LOSS_TO_WIN,
    MIN_TOTAL_TRADES,
    MIN_WIN_RATE,
    REACTIVATE_WIN_RATE,
    REACTIVATE_PROFIT_FACTOR,
    REACTIVATE_SHARPE,
    REACTIVATE_MIN_TRADES,
    LOOKBACK_TRADES,
)


def evaluate_bot(metrics, bot_status):
    """
    Evaluate bot metrics against configured thresholds.

    Args:
        metrics (dict): Bot performance metrics including:
            - total_trades: int
            - win_rate: float (0-100)
            - max_drawdown: float (negative value like -20.5)
            - profit_factor: float
            - consecutive_losses: int
            - sharpe_ratio: float
            - avg_loss: float
            - avg_win: float
            - recent_trades: list[dict] with 'win' boolean keys

        bot_status (str): Current bot state ('active', 'paused', etc.)

    Returns:
        dict with keys:
            - verdict: str ('PAUSE', 'HOLD', 'REACTIVATE', 'INSUFFICIENT_DATA')
            - reasons: list[str] (human-readable decision rationale)
            - metrics_snapshot: dict (copy of evaluated metrics)
    """

    reasons = []
    verdict = "HOLD"

    # Step 1: Check minimum trades threshold
    total_trades = metrics.get("total_trades", 0)
    if total_trades < MIN_TOTAL_TRADES:
        verdict = "INSUFFICIENT_DATA"
        reasons.append(f"Only {total_trades} trades, need at least {MIN_TOTAL_TRADES}")
        return {
            "verdict": verdict,
            "reasons": reasons,
            "metrics_snapshot": dict(metrics),
        }

    # Step 2: If paused, check reactivation criteria (ALL must pass)
    if bot_status == "paused":
        reactivation_checks = _check_reactivation_criteria(metrics, reasons)
        if reactivation_checks["all_pass"]:
            verdict = "REACTIVATE"
            reasons = reactivation_checks["reasons"]
        else:
            verdict = "HOLD"
            reasons = reactivation_checks["reasons"]

        return {
            "verdict": verdict,
            "reasons": reasons,
            "metrics_snapshot": dict(metrics),
        }

    # Step 3: If active, check pause triggers (ANY ONE triggers PAUSE)
    if bot_status == "active":
        pause_triggers = _check_pause_triggers(metrics, reasons)
        if pause_triggers["triggered"]:
            verdict = "PAUSE"
            reasons = pause_triggers["reasons"]
        else:
            verdict = "HOLD"
            reasons.append("All metrics within acceptable ranges")

        return {
            "verdict": verdict,
            "reasons": reasons,
            "metrics_snapshot": dict(metrics),
        }

    # Default case: unknown status
    return {
        "verdict": "HOLD",
        "reasons": ["Unknown bot status; holding current position"],
        "metrics_snapshot": dict(metrics),
    }


def _trade_is_win(trade):
    """Interpret recent-trade rows from normalized or raw source shapes."""
    if "win" in trade:
        return bool(trade.get("win"))
    pnl = trade.get("pnl", trade.get("profit", 0))
    try:
        return float(pnl) >= 0
    except (TypeError, ValueError):
        return False


def _check_reactivation_criteria(metrics, reasons):
    """
    Check if paused bot meets ALL reactivation criteria.

    Returns:
        dict with 'all_pass' (bool) and 'reasons' (list[str])
    """
    checks_passed = []

    # Check 1: Win rate >= 52.0
    win_rate = metrics.get("win_rate", 0)
    if win_rate >= REACTIVATE_WIN_RATE:
        checks_passed.append(True)
    else:
        checks_passed.append(False)
        reasons.append(
            f"Win rate {win_rate:.1f}% is below reactivation threshold of {REACTIVATE_WIN_RATE}%"
        )

    # Check 2: Profit factor >= 1.2
    profit_factor = metrics.get("profit_factor", 0)
    if profit_factor >= REACTIVATE_PROFIT_FACTOR:
        checks_passed.append(True)
    else:
        checks_passed.append(False)
        reasons.append(
            f"Profit factor {profit_factor:.2f} is below reactivation threshold of {REACTIVATE_PROFIT_FACTOR}"
        )

    # Check 3: Sharpe ratio >= 0.6
    sharpe_ratio = metrics.get("sharpe_ratio", 0)
    if sharpe_ratio >= REACTIVATE_SHARPE:
        checks_passed.append(True)
    else:
        checks_passed.append(False)
        reasons.append(
            f"Sharpe ratio {sharpe_ratio:.2f} is below reactivation threshold of {REACTIVATE_SHARPE}"
        )

    # Check 4: Total trades >= 20
    total_trades = metrics.get("total_trades", 0)
    if total_trades >= REACTIVATE_MIN_TRADES:
        checks_passed.append(True)
    else:
        checks_passed.append(False)
        reasons.append(
            f"Only {total_trades} trades since pause, need at least {REACTIVATE_MIN_TRADES}"
        )

    return {
        "all_pass": all(checks_passed),
        "reasons": reasons if not all(checks_passed) else ["All reactivation criteria met"],
    }


def _check_pause_triggers(metrics, reasons):
    """
    Check if active bot triggers ANY pause condition.

    Returns:
        dict with 'triggered' (bool) and 'reasons' (list[str])
    """
    triggered = False
    local_reasons = list(reasons)

    # Trigger 1: Win rate too low
    win_rate = metrics.get("win_rate", 0)
    if win_rate < PAUSE_WIN_RATE:
        triggered = True
        local_reasons.append(f"Win rate {win_rate:.1f}% is below {PAUSE_WIN_RATE}%")

    # Trigger 2: Max drawdown too severe
    max_drawdown = metrics.get("max_drawdown", 0)
    if max_drawdown < PAUSE_MAX_DRAWDOWN:
        triggered = True
        local_reasons.append(
            f"Max drawdown {max_drawdown:.1f}% exceeds threshold of {PAUSE_MAX_DRAWDOWN}%"
        )

    # Trigger 3: Profit factor too low (but only if positive)
    profit_factor = metrics.get("profit_factor", 0)
    if profit_factor > 0 and profit_factor < PAUSE_PROFIT_FACTOR:
        triggered = True
        local_reasons.append(f"Profit factor {profit_factor:.2f} below {PAUSE_PROFIT_FACTOR}")

    # Trigger 4: Consecutive losses
    consecutive_losses = metrics.get("consecutive_losses", 0)
    if consecutive_losses >= PAUSE_CONSECUTIVE_LOSSES:
        triggered = True
        local_reasons.append(
            f"{consecutive_losses} consecutive losses (limit: {PAUSE_CONSECUTIVE_LOSSES})"
        )

    # Trigger 5: Sharpe ratio too low (but not if it's exactly 0)
    sharpe_ratio = metrics.get("sharpe_ratio", 0)
    if sharpe_ratio != 0 and sharpe_ratio < PAUSE_SHARPE_RATIO:
        triggered = True
        local_reasons.append(f"Sharpe ratio {sharpe_ratio:.2f} below {PAUSE_SHARPE_RATIO}")

    # Trigger 6: Average loss to win ratio too high (only if avg_win > 0)
    avg_loss = metrics.get("avg_loss", 0)
    avg_win = metrics.get("avg_win", 0)
    if avg_win > 0:
        avg_loss_to_win = avg_loss / avg_win if avg_win != 0 else 0
        if avg_loss_to_win > PAUSE_AVG_LOSS_TO_WIN:
            triggered = True
            local_reasons.append(
                f"Avg loss/win ratio {avg_loss_to_win:.2f} exceeds {PAUSE_AVG_LOSS_TO_WIN}"
            )

    # Trigger 7: Recent win rate degradation (last 20 trades if available)
    recent_trades = metrics.get("recent_trades", [])
    if len(recent_trades) >= 10:
        window = recent_trades[-LOOKBACK_TRADES:]
        recent_wins = sum(1 for t in window if _trade_is_win(t))
        recent_win_rate = (recent_wins / len(window) * 100) if window else 0
        win_rate_floor = MIN_WIN_RATE - 5
        if recent_win_rate < win_rate_floor:
            triggered = True
            local_reasons.append(
                f"Recent {LOOKBACK_TRADES}-trade win rate {recent_win_rate:.1f}% below floor of {win_rate_floor}%"
            )

    return {
        "triggered": triggered,
        "reasons": local_reasons,
    }


def format_verdict_report(bot_name, verdict_result):
    """
    Format verdict result into human-readable console output.

    Args:
        bot_name (str): Name of the bot
        verdict_result (dict): Result from evaluate_bot()

    Returns:
        str: Formatted report
    """
    verdict = verdict_result["verdict"]
    reasons = verdict_result["reasons"]

    # Color coding for verdicts (ANSI codes)
    verdict_colors = {
        "PAUSE": "\033[91m",  # Red
        "REACTIVATE": "\033[92m",  # Green
        "HOLD": "\033[93m",  # Yellow
        "INSUFFICIENT_DATA": "\033[94m",  # Blue
    }
    reset_color = "\033[0m"

    color = verdict_colors.get(verdict, "")

    report = (
        f"\n{'='*70}\n"
        f"BOT DECISION REPORT: {bot_name}\n"
        f"{'='*70}\n"
        f"Verdict: {color}{verdict}{reset_color}\n"
        f"{'-'*70}\n"
        f"Reasoning:\n"
    )

    for i, reason in enumerate(reasons, 1):
        report += f"  {i}. {reason}\n"

    report += f"{'='*70}\n"

    return report
