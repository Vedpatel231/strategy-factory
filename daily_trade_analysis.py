"""
Daily Trade Analysis — Automated performance review.

Pulls the last 24h of closed trades from the running dashboard API,
analyzes win/loss patterns by strategy, regime, confidence, exit type,
and generates a plain-text summary report.

Can be run standalone:
    python daily_trade_analysis.py

Or imported by scheduled tasks for automated daily reporting.

Env vars (set in .env or Railway):
    DASHBOARD_URL       — e.g. https://strategy-factory-production-9843.up.railway.app
    DASHBOARD_USERNAME  — HTTP basic auth user  (default: admin)
    DASHBOARD_PASSWORD  — HTTP basic auth password
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger("daily_trade_analysis")

# ── Config ─────────────────────────────────────────────────────────────
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://strategy-factory-production-9843.up.railway.app")
DASHBOARD_USER = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASS = os.getenv("DASHBOARD_PASSWORD", "")
REPORT_DIR = Path(os.getenv("REPORT_DIR", "/data/reports"))

# ── API helpers ────────────────────────────────────────────────────────

def _is_local():
    """Return True if running inside the same process as dashboard_server (Railway)."""
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RENDER"))


def _api_get(path, params=None):
    """GET from dashboard API with basic auth."""
    import requests
    url = f"{DASHBOARD_URL.rstrip('/')}{path}"
    auth = (DASHBOARD_USER, DASHBOARD_PASS) if DASHBOARD_PASS else None
    resp = requests.get(url, params=params, auth=auth, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _local_trade_ledger(limit=500):
    """Fetch trade ledger directly (when running on Railway in same process)."""
    try:
        from trade_journal import load_trade_ledger, rebuild_trade_ledger_from_journal
        rebuild_trade_ledger_from_journal()
        rows = load_trade_ledger(limit=limit) or []
        net_values = [float(r.get("net_pl") or 0) for r in rows if r]
        wins = sum(1 for v in net_values if v > 0)
        losses = sum(1 for v in net_values if v < 0)
        return {
            "rows": rows,
            "summary": {
                "trades": len(rows), "wins": wins, "losses": losses,
                "win_rate": round(wins / len(rows) * 100, 1) if rows else None,
                "net_pl": round(sum(net_values), 2),
            }
        }
    except Exception as e:
        logger.error(f"Local trade ledger fetch failed: {e}", exc_info=True)
        return {"rows": [], "summary": {}}


def _local_account():
    """Fetch account directly via Alpaca client."""
    try:
        from alpaca_client import AlpacaPaperClient
        client = AlpacaPaperClient()
        result = client.get_account()
        return result if result else {}
    except Exception as e:
        logger.error(f"Local account fetch failed: {e}", exc_info=True)
        return {}


def _local_positions():
    """Fetch positions directly via Alpaca client."""
    try:
        from alpaca_client import AlpacaPaperClient
        client = AlpacaPaperClient()
        positions = client.get_positions() or []
        total_cost = sum(float(p.get("cost_basis", 0) or 0) for p in positions)
        total_mv = sum(float(p.get("market_value", 0) or 0) for p in positions)
        total_upl = sum(float(p.get("unrealized_pl", 0) or 0) for p in positions)
        return {
            "positions": positions,
            "summary": {
                "count": len(positions),
                "total_cost_basis": round(total_cost, 2),
                "total_market_value": round(total_mv, 2),
                "total_unrealized_pl": round(total_upl, 2),
                "total_unrealized_plpc": round(total_upl / total_cost * 100, 2) if total_cost else 0,
            }
        }
    except Exception as e:
        logger.error(f"Local positions fetch failed: {e}", exc_info=True)
        return {"positions": [], "summary": {}}


def fetch_trade_ledger(limit=500):
    """Fetch the full trade ledger."""
    if _is_local():
        return _local_trade_ledger(limit)
    return _api_get("/api/alpaca/trade-ledger", {"limit": limit})


def fetch_account():
    """Fetch current account snapshot."""
    if _is_local():
        return _local_account()
    return _api_get("/api/alpaca/account")


def fetch_positions():
    """Fetch open positions."""
    if _is_local():
        return _local_positions()
    return _api_get("/api/alpaca/positions")


def fetch_auto_status():
    """Fetch auto-trader status."""
    if _is_local():
        try:
            from alpaca_auto_trader import AlpacaAutoTrader
            return AlpacaAutoTrader.get().status()
        except Exception:
            return {}
    return _api_get("/api/alpaca/auto/status")


# ── Analysis ───────────────────────────────────────────────────────────

def filter_last_24h(rows, hours=24):
    """Return only trades closed within the last N hours."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent = []
    for r in rows:
        closed = r.get("closed_at", "")
        if not closed:
            continue
        try:
            dt = datetime.fromisoformat(closed.replace("Z", "+00:00"))
            if dt >= cutoff:
                recent.append(r)
        except (ValueError, TypeError):
            continue
    return recent


def analyze_trades(rows):
    """Full analysis of a set of closed trades.
    Returns a dict with all analysis sections."""

    if not rows:
        return {"empty": True, "message": "No closed trades in this period."}

    analysis = {
        "total_trades": len(rows),
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "net_pl": 0.0,
        "gross_pl": 0.0,
        "total_fees": 0.0,
        "by_strategy": {},
        "by_regime": {},
        "by_exit_type": {},
        "by_confidence": {"<0.7": {"count": 0, "net_pl": 0}, "0.7-0.85": {"count": 0, "net_pl": 0},
                          "0.85-1.0": {"count": 0, "net_pl": 0}, "1.0": {"count": 0, "net_pl": 0}},
        "avg_hold_hours": 0.0,
        "best_trade": None,
        "worst_trade": None,
        "trades": [],
    }

    total_hours = 0.0

    for r in rows:
        net = float(r.get("net_pl", 0) or 0)
        gross = float(r.get("gross_pl", 0) or 0)
        fees = float(r.get("total_fees", 0) or 0)
        conf = float(r.get("confidence", 0) or 0)
        strat = r.get("strategy", "unknown")
        regime = r.get("regime", "unknown")
        exit_reason = r.get("exit_reason", "unknown")
        symbol = r.get("symbol", "?")

        # Holding time
        try:
            opened = datetime.fromisoformat(r["opened_at"].replace("Z", "+00:00"))
            closed = datetime.fromisoformat(r["closed_at"].replace("Z", "+00:00"))
            hold_hours = (closed - opened).total_seconds() / 3600
        except (KeyError, ValueError, TypeError):
            hold_hours = 0
        total_hours += hold_hours

        # Win/loss
        if net > 0:
            analysis["wins"] += 1
        elif net < 0:
            analysis["losses"] += 1
        else:
            analysis["breakeven"] += 1

        analysis["net_pl"] += net
        analysis["gross_pl"] += gross
        analysis["total_fees"] += fees

        # By strategy
        if strat not in analysis["by_strategy"]:
            analysis["by_strategy"][strat] = {"count": 0, "wins": 0, "net_pl": 0.0, "fees": 0.0}
        analysis["by_strategy"][strat]["count"] += 1
        analysis["by_strategy"][strat]["net_pl"] += net
        analysis["by_strategy"][strat]["fees"] += fees
        if net > 0:
            analysis["by_strategy"][strat]["wins"] += 1

        # By regime
        if regime not in analysis["by_regime"]:
            analysis["by_regime"][regime] = {"count": 0, "wins": 0, "net_pl": 0.0}
        analysis["by_regime"][regime]["count"] += 1
        analysis["by_regime"][regime]["net_pl"] += net
        if net > 0:
            analysis["by_regime"][regime]["wins"] += 1

        # By exit type
        exit_type = _classify_exit(exit_reason)
        if exit_type not in analysis["by_exit_type"]:
            analysis["by_exit_type"][exit_type] = {"count": 0, "net_pl": 0.0, "gross_pl": 0.0, "fees": 0.0}
        analysis["by_exit_type"][exit_type]["count"] += 1
        analysis["by_exit_type"][exit_type]["net_pl"] += net
        analysis["by_exit_type"][exit_type]["gross_pl"] += gross
        analysis["by_exit_type"][exit_type]["fees"] += fees

        # By confidence bucket
        if conf >= 1.0:
            bucket = "1.0"
        elif conf >= 0.85:
            bucket = "0.85-1.0"
        elif conf >= 0.7:
            bucket = "0.7-0.85"
        else:
            bucket = "<0.7"
        analysis["by_confidence"][bucket]["count"] += 1
        analysis["by_confidence"][bucket]["net_pl"] += net

        # Best/worst
        trade_summary = {"symbol": symbol, "strategy": strat, "regime": regime,
                         "confidence": conf, "net_pl": net, "hold_hours": round(hold_hours, 1),
                         "exit_reason": exit_reason}
        analysis["trades"].append(trade_summary)

        if analysis["best_trade"] is None or net > analysis["best_trade"]["net_pl"]:
            analysis["best_trade"] = trade_summary
        if analysis["worst_trade"] is None or net < analysis["worst_trade"]["net_pl"]:
            analysis["worst_trade"] = trade_summary

    analysis["net_pl"] = round(analysis["net_pl"], 2)
    analysis["gross_pl"] = round(analysis["gross_pl"], 2)
    analysis["total_fees"] = round(analysis["total_fees"], 2)
    analysis["avg_hold_hours"] = round(total_hours / len(rows), 1) if rows else 0
    analysis["win_rate"] = round(analysis["wins"] / len(rows) * 100, 1) if rows else 0

    # Round sub-dicts
    for d in analysis["by_strategy"].values():
        d["net_pl"] = round(d["net_pl"], 2)
        d["fees"] = round(d["fees"], 2)
    for d in analysis["by_regime"].values():
        d["net_pl"] = round(d["net_pl"], 2)
    for d in analysis["by_exit_type"].values():
        d["net_pl"] = round(d["net_pl"], 2)
        d["gross_pl"] = round(d["gross_pl"], 2)
        d["fees"] = round(d["fees"], 2)
    for d in analysis["by_confidence"].values():
        d["net_pl"] = round(d["net_pl"], 2)

    return analysis


def _classify_exit(reason):
    if not reason:
        return "Unknown"
    r = reason.lower()
    if "stale" in r or "timeout" in r:
        return "Stale/Timeout"
    if "stop loss" in r:
        return "Stop Loss"
    if "trailing" in r:
        return "Trailing Stop"
    if "take profit" in r:
        return "Take Profit"
    if "early timeout" in r:
        return "Early Timeout"
    return "Other"


# ── Recommendations engine ─────────────────────────────────────────────

def generate_recommendations(analysis, all_time_analysis=None):
    """Generate actionable recommendations from analysis."""
    recs = []

    if analysis.get("empty"):
        return ["No trades to analyze. System may be idle or filters are too tight."]

    # Strategy-level recs
    for strat, data in analysis["by_strategy"].items():
        if data["count"] >= 3 and data["wins"] == 0:
            recs.append(f"BLOCK '{strat}': {data['count']} trades, 0 wins, ${data['net_pl']:.0f} net loss.")
        elif data["count"] >= 3 and data["wins"] / data["count"] < 0.2 and data["net_pl"] < -100:
            recs.append(f"PENALIZE '{strat}': {data['wins']}/{data['count']} win rate, ${data['net_pl']:.0f} net loss.")

    # Regime-level recs
    for regime, data in analysis["by_regime"].items():
        if data["count"] >= 2 and data["wins"] == 0:
            recs.append(f"AVOID '{regime}' regime: {data['count']} trades, 0 wins, ${data['net_pl']:.0f} net.")

    # Fee drag warning
    if analysis["total_fees"] > 0 and analysis["gross_pl"] != 0:
        fee_pct_of_gross = abs(analysis["total_fees"] / analysis["gross_pl"] * 100) if analysis["gross_pl"] != 0 else 100
        if fee_pct_of_gross > 20:
            recs.append(f"FEE DRAG: Fees are {fee_pct_of_gross:.0f}% of gross P&L (${analysis['total_fees']:.0f} fees on ${analysis['gross_pl']:.0f} gross).")

    # Confidence calibration
    if analysis["by_confidence"]["1.0"]["count"] >= 2 and analysis["by_confidence"]["1.0"]["net_pl"] < -50:
        recs.append(f"CONFIDENCE BROKEN: Max-confidence trades lost ${analysis['by_confidence']['1.0']['net_pl']:.0f}. Model needs recalibration.")

    # Win rate warning
    if analysis["win_rate"] < 20 and analysis["total_trades"] >= 5:
        recs.append(f"CRITICAL: Win rate is {analysis['win_rate']}% across {analysis['total_trades']} trades. Consider pausing the system.")

    if not recs:
        recs.append("No critical issues detected. System performing within expected parameters.")

    return recs


# ── Report generation ──────────────────────────────────────────────────

def format_report(analysis, account=None, positions=None, auto_status=None, recommendations=None):
    """Generate a plain-text daily summary report."""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append("=" * 64)
    lines.append(f"  DAILY TRADING ANALYSIS — {now}")
    lines.append("=" * 64)
    lines.append("")

    # Account snapshot
    if account:
        lines.append("── ACCOUNT ──────────────────────────────────────────")
        lines.append(f"  Equity:       ${account.get('equity', 0):,.2f}")
        lines.append(f"  Cash:         ${account.get('cash', 0):,.2f}")
        lines.append(f"  Day P&L:      ${account.get('total_pl', 0):,.2f} ({account.get('total_pl_pct', 0):+.2f}%)")
        lines.append(f"  Status:       {account.get('status', 'UNKNOWN')}")
        lines.append("")

    # Open positions
    if positions:
        pos_list = positions.get("positions", [])
        summary = positions.get("summary", {})
        lines.append(f"── OPEN POSITIONS ({summary.get('count', len(pos_list))}) ─────────────────────────")
        lines.append(f"  Total Cost:   ${summary.get('total_cost_basis', 0):,.2f}")
        lines.append(f"  Market Value: ${summary.get('total_market_value', 0):,.2f}")
        lines.append(f"  Unrealized:   ${summary.get('total_unrealized_pl', 0):,.2f} ({summary.get('total_unrealized_plpc', 0):+.2f}%)")
        for p in pos_list:
            lines.append(f"    {p['symbol']:12s}  entry ${p['avg_entry_price']:<10.4f}  P&L ${p['unrealized_pl']:+8.2f} ({p['unrealized_plpc']:+.1f}%)")
        lines.append("")

    # Auto-trader status
    if auto_status:
        lines.append("── AUTO-TRADER ──────────────────────────────────────")
        lines.append(f"  Enabled:      {auto_status.get('enabled', False)}")
        lines.append(f"  Interval:     {auto_status.get('interval_min', '?')} min")
        lines.append(f"  Last run:     {auto_status.get('last_run', 'never')}")
        lines.append(f"  Last error:   {auto_status.get('last_error', 'none')}")
        last_result = auto_status.get("last_result", {})
        trade_summary = last_result.get("steps", {}).get("trade", {}).get("summary", {})
        if trade_summary:
            lines.append(f"  Last cycle:   {trade_summary.get('total_orders', 0)} orders, {trade_summary.get('buys', 0)} buys, {trade_summary.get('sells', 0)} sells, {trade_summary.get('skipped', 0)} skipped")
        lines.append("")

    if analysis.get("empty"):
        lines.append("── TRADES (last 24h) ────────────────────────────────")
        lines.append(f"  {analysis['message']}")
        lines.append("")
    else:
        # Trade summary
        lines.append("── TRADES (last 24h) ────────────────────────────────")
        lines.append(f"  Total:        {analysis['total_trades']}")
        lines.append(f"  Wins:         {analysis['wins']}  |  Losses: {analysis['losses']}")
        lines.append(f"  Win Rate:     {analysis['win_rate']}%")
        lines.append(f"  Net P&L:      ${analysis['net_pl']:+,.2f}")
        lines.append(f"  Gross P&L:    ${analysis['gross_pl']:+,.2f}")
        lines.append(f"  Total Fees:   ${analysis['total_fees']:,.2f}")
        lines.append(f"  Avg Hold:     {analysis['avg_hold_hours']}h")
        lines.append("")

        if analysis.get("best_trade"):
            t = analysis["best_trade"]
            lines.append(f"  Best:   {t['symbol']} ({t['strategy']}/{t['regime']}) ${t['net_pl']:+.2f} in {t['hold_hours']}h")
        if analysis.get("worst_trade"):
            t = analysis["worst_trade"]
            lines.append(f"  Worst:  {t['symbol']} ({t['strategy']}/{t['regime']}) ${t['net_pl']:+.2f} in {t['hold_hours']}h")
        lines.append("")

        # By strategy
        lines.append("── BY STRATEGY ──────────────────────────────────────")
        for strat, data in sorted(analysis["by_strategy"].items(), key=lambda x: x[1]["net_pl"]):
            wr = round(data["wins"] / data["count"] * 100) if data["count"] else 0
            lines.append(f"  {strat:25s}  {data['count']:2d} trades  {data['wins']:2d}W  ${data['net_pl']:+8.2f}  ({wr}% WR)  fees: ${data['fees']:.2f}")
        lines.append("")

        # By regime
        lines.append("── BY REGIME ────────────────────────────────────────")
        for regime, data in sorted(analysis["by_regime"].items(), key=lambda x: x[1]["net_pl"]):
            wr = round(data["wins"] / data["count"] * 100) if data["count"] else 0
            lines.append(f"  {regime:25s}  {data['count']:2d} trades  {data['wins']:2d}W  ${data['net_pl']:+8.2f}  ({wr}% WR)")
        lines.append("")

        # By exit type
        lines.append("── BY EXIT TYPE ─────────────────────────────────────")
        for exit_type, data in sorted(analysis["by_exit_type"].items(), key=lambda x: x[1]["net_pl"]):
            lines.append(f"  {exit_type:18s}  {data['count']:2d} trades  net ${data['net_pl']:+8.2f}  gross ${data['gross_pl']:+8.2f}  fees ${data['fees']:.2f}")
        lines.append("")

        # By confidence
        lines.append("── BY CONFIDENCE ────────────────────────────────────")
        for bucket in ["<0.7", "0.7-0.85", "0.85-1.0", "1.0"]:
            data = analysis["by_confidence"][bucket]
            if data["count"] > 0:
                lines.append(f"  {bucket:12s}  {data['count']:2d} trades  ${data['net_pl']:+8.2f}")
        lines.append("")

    # Recommendations
    if recommendations:
        lines.append("── RECOMMENDATIONS ──────────────────────────────────")
        for i, rec in enumerate(recommendations, 1):
            lines.append(f"  {i}. {rec}")
        lines.append("")

    lines.append("=" * 64)
    lines.append(f"  Report generated: {now}")
    lines.append("=" * 64)

    return "\n".join(lines)


# ── Main entry point ───────────────────────────────────────────────────

def run_daily_analysis(hours=24, save_report=True):
    """Run the full daily analysis pipeline. Returns (report_text, analysis_dict)."""
    logger.info(f"Starting daily trade analysis (last {hours}h)...")

    # Fetch data — each call is wrapped individually so one failure
    # doesn't kill the whole report.
    ledger, account, positions, auto_status = None, None, None, None
    try:
        ledger = fetch_trade_ledger(limit=500)
    except Exception as e:
        logger.error(f"Failed to fetch trade ledger: {e}", exc_info=True)
    try:
        account = fetch_account()
    except Exception as e:
        logger.error(f"Failed to fetch account: {e}", exc_info=True)
    try:
        positions = fetch_positions()
    except Exception as e:
        logger.error(f"Failed to fetch positions: {e}", exc_info=True)
    try:
        auto_status = fetch_auto_status()
    except Exception as e:
        logger.error(f"Failed to fetch auto status: {e}", exc_info=True)

    # Guard against None returns
    if ledger is None:
        ledger = {"rows": [], "summary": {}}
    if account is None:
        account = {}
    if positions is None:
        positions = {"positions": [], "summary": {}}
    if auto_status is None:
        auto_status = {}

    # Filter and analyze
    all_rows = ledger.get("rows", []) or []
    recent_rows = filter_last_24h(all_rows, hours=hours)

    analysis = analyze_trades(recent_rows)
    all_time = analyze_trades(all_rows)
    recommendations = generate_recommendations(analysis, all_time_analysis=all_time)

    # Generate report
    report = format_report(analysis, account, positions, auto_status, recommendations)

    # Save
    if save_report:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        report_path = REPORT_DIR / f"daily_analysis_{date_str}.txt"
        report_path.write_text(report, encoding="utf-8")
        logger.info(f"Report saved to {report_path}")

        # Also save JSON for programmatic access
        json_path = REPORT_DIR / f"daily_analysis_{date_str}.json"
        json_path.write_text(json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period_hours": hours,
            "analysis": analysis,
            "all_time_summary": ledger.get("summary", {}),
            "recommendations": recommendations,
            "account": account,
        }, indent=2, default=str), encoding="utf-8")

    return report, analysis


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

    # Try to load env vars
    try:
        import env_loader  # noqa: F401
    except ImportError:
        pass

    report, analysis = run_daily_analysis()
    print(report)
