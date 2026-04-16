"""
Strategy Factory Bot Manager — Daily Runner (Master Orchestrator)
Supports three execution modes:
  python daily_runner.py              → dry run (reporting only)
  python daily_runner.py --execute    → live mode (pauses/reactivates bots)
  python daily_runner.py --dump-raw   → debugging (dumps raw data)
"""

import argparse
import json
import logging
import os
import sys
import datetime
import traceback

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 fallback
    ZoneInfo = None

# Add project dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from api_client import StrategyFactoryClient
from analytics import StrategyMetrics
from decision_engine import evaluate_bot, format_verdict_report
from learning_engine import LearningEngine
from generate_dashboard import DashboardGenerator
from portfolio_allocator import allocate_portfolio

# ── ANSI Colors ──────────────────────────────────────────────────────
class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    GRAY    = "\033[90m"
    WHITE   = "\033[97m"

VERDICT_COLORS = {
    "PAUSE": C.RED,
    "HOLD": C.YELLOW,
    "REACTIVATE": C.GREEN,
    "INSUFFICIENT_DATA": C.GRAY,
}


def setup_logging(verbose=False):
    """Configure logging to both file and console."""
    os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt,
                        handlers=[
                            logging.FileHandler(config.LOG_FILE),
                            logging.StreamHandler(sys.stdout)
                        ])
    return logging.getLogger("strategy_factory")


def timestamp():
    return datetime.datetime.utcnow().strftime("%H:%M:%S")


def print_header():
    print(f"\n{C.CYAN}{C.BOLD}" + "=" * 64)
    print("  ⚡ Strategy Factory — Daily Bot Review")
    print("  " + datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 64 + C.RESET + "\n")


def print_step(num, text):
    print(f"  {C.CYAN}[{timestamp()}]{C.RESET} Step {num}: {text}")


def print_verdict(bot_name, pair, base_verdict, enhanced_verdict, reasons, adapt_score):
    v = enhanced_verdict.upper()
    color = VERDICT_COLORS.get(v, C.WHITE)
    override_marker = f" {C.MAGENTA}(override from {base_verdict}){C.RESET}" if base_verdict.upper() != v else ""
    print(f"    {C.BOLD}{bot_name:<28}{C.RESET} {C.CYAN}{pair:<12}{C.RESET} "
          f"→ {color}{C.BOLD}{v:<18}{C.RESET}{override_marker}  "
          f"Adapt: {adapt_score:.0f}")
    for r in reasons[:3]:
        print(f"      {C.GRAY}• {r}{C.RESET}")


def run_analysis(args, logger):
    """Main analysis pipeline."""
    mode = "EXECUTE" if args.execute else "DUMP-RAW" if args.dump_raw else "DRY RUN"
    print(f"  {C.YELLOW}Mode: {mode}{C.RESET}\n")

    # ── Step 1: Initialize components ────────────────────────────────
    print_step(1, "Initializing components...")
    client = StrategyFactoryClient()
    learner = LearningEngine()
    dashboard_gen = DashboardGenerator()
    logger.info("All components initialized")

    # ── Step 2: Fetch all bots ───────────────────────────────────────
    print_step(2, "Fetching all bots from database...")
    bots = client.get_my_bots()
    if not bots:
        print(f"  {C.RED}No bots found! Run seed_data.py first.{C.RESET}")
        return
    print(f"    Found {C.BOLD}{len(bots)}{C.RESET} bots")
    logger.info(f"Fetched {len(bots)} bots")

    # ── Step 3: Fetch strategy data for each bot ─────────────────────
    print_step(3, "Fetching strategy data and building metrics...")
    bot_data_list = []
    equity_curves = []
    strategies_raw = {}

    for bot in bots:
        strategy_id = bot.get("strategy_id")
        if strategy_id and strategy_id not in strategies_raw:
            strat = client.get_strategy(strategy_id)
            strategies_raw[strategy_id] = strat

        strat_data = strategies_raw.get(strategy_id, {})
        metrics = StrategyMetrics(strat_data)

        if args.dump_raw:
            print(f"\n    {C.CYAN}--- RAW: {bot.get('name')} ---{C.RESET}")
            print(json.dumps(strat_data, indent=2, default=str)[:2000])

        # Build equity curve from PnL history
        perf_history = strat_data.get("performance_history", [])
        curve = [row.get("pnl", 0) for row in perf_history] if perf_history else []
        equity_curves.append(curve)

        bot_data_list.append({
            "bot": bot,
            "strategy": strat_data,
            "metrics": metrics,
            "metrics_dict": metrics.to_dict(),
        })

    print(f"    Built metrics for {C.BOLD}{len(bot_data_list)}{C.RESET} bot-strategy pairs")

    if args.dump_raw:
        print(f"\n  {C.YELLOW}Dump complete. Exiting.{C.RESET}")
        return

    # ── Step 4: Detect market regime ─────────────────────────────────
    print_step(4, "Detecting market regime...")
    regime_info = learner.detect_regime(equity_curves)
    regime = regime_info.get("regime", "unknown")
    regime_conf = regime_info.get("confidence", 0)
    regime_color = C.GREEN if "up" in regime else C.RED if "down" in regime else C.YELLOW
    print(f"    Regime: {regime_color}{C.BOLD}{regime.replace('_', ' ').title()}{C.RESET} "
          f"(confidence: {regime_conf:.0%})")
    logger.info(f"Market regime: {regime} ({regime_conf:.0%})")

    # ── Step 5: Run learning engine ──────────────────────────────────
    print_step(5, "Running adaptive learning engine...")
    # Compute adaptation scores
    for bd in bot_data_list:
        sid = str(bd["bot"].get("strategy_id", bd["bot"].get("id", "")))
        adapt_result = learner.compute_adaptation_score(
            bd["metrics_dict"], regime, sid
        )
        bd["adapt_result"] = adapt_result
        bd["adaptation_score"] = adapt_result.get("score", 50)
        bd["adaptation_label"] = adapt_result.get("label", "NEUTRAL")

        # Update regime-strategy performance tracking
        learner.update_regime_performance(sid, regime, bd["metrics_dict"])

    # Run hindsight analysis on previously paused strategies
    current_metrics_map = {}
    for bd in bot_data_list:
        sid = str(bd["bot"].get("strategy_id", bd["bot"].get("id", "")))
        current_metrics_map[sid] = bd["metrics_dict"]
    learner.review_pause_events(current_metrics_map)

    print(f"    Adaptation scores computed for all strategies")
    logger.info("Learning engine complete")

    # ── Step 6: Evaluate each bot ────────────────────────────────────
    print_step(6, "Evaluating bots (decision engine + learning overrides)...")
    print()
    evaluations = []
    counts = {"PAUSE": 0, "HOLD": 0, "REACTIVATE": 0, "INSUFFICIENT_DATA": 0}
    actions_taken = []

    for bd in bot_data_list:
        bot = bd["bot"]
        m = bd["metrics_dict"]
        bot_status = bot.get("status", "active")

        # Base verdict from decision engine
        base_result = evaluate_bot(m, bot_status)
        base_verdict = base_result.get("verdict", "HOLD")

        # Enhanced verdict from learning engine
        sid = str(bot.get("strategy_id", bot.get("id", "")))
        enhanced_result = learner.enhanced_verdict(
            base_verdict, bd["adapt_result"], sid, bot_status
        )
        enhanced_verdict = enhanced_result.get("verdict", base_verdict)
        all_reasons = base_result.get("reasons", []) + enhanced_result.get("reasons", [])

        # Print verdict
        print_verdict(bot.get("name", "?"), bot.get("pair", ""),
                     base_verdict, enhanced_verdict, all_reasons, bd["adaptation_score"])

        # Track counts
        counts[enhanced_verdict.upper()] = counts.get(enhanced_verdict.upper(), 0) + 1

        # Execute if in live mode
        if args.execute and enhanced_verdict.upper() in ("PAUSE", "REACTIVATE"):
            action = enhanced_verdict.upper()
            if action == "PAUSE":
                result = client.pause_bot(bot.get("id"))
                learner.record_pause_event(
                    str(bot.get("id")), sid, m, regime
                )
            else:
                result = client.reactivate_bot(bot.get("id"))
            actions_taken.append({"bot": bot.get("name"), "action": action, "result": result})
            print(f"      {C.MAGENTA}⚡ EXECUTED: {action}{C.RESET}")

        evaluations.append({
            "bot_id": bot.get("id"),
            "bot_name": bot.get("name", "?"),
            "pair": bot.get("pair", ""),
            "strategy_type": bot.get("strategy_type", ""),
            "bot_status": bot_status,
            "verdict": base_verdict,
            "enhanced_verdict": enhanced_verdict,
            "adaptation_score": bd["adaptation_score"],
            "adaptation_label": bd["adaptation_label"],
            "reasons": all_reasons,
            "metrics": m,
        })

    # ── Step 7: Save learning state ──────────────────────────────────
    print()
    print_step(7, "Saving learning state...")
    learner.save_state()
    logger.info("Learning state saved")

    # ── Step 7.5: Portfolio allocation ──────────────────────────────
    print_step("7b", "Allocating $1,000 portfolio across strategies...")
    portfolio = allocate_portfolio(1000.0, evaluations)
    n_alloc = portfolio["summary"]["num_strategies"]
    exp_return = portfolio["summary"].get("expected_monthly_return_pct", 0)
    print(f"    Allocated across {C.BOLD}{n_alloc}{C.RESET} strategies")
    print(f"    Expected monthly return: {C.GREEN}{exp_return:+.1f}%{C.RESET}")
    for a in portfolio["allocations"][:5]:
        print(f"      ${a['allocation_usd']:>7.2f} ({a['allocation_pct']:>4.1f}%) → {a['bot_name']}")
    if len(portfolio["allocations"]) > 5:
        print(f"      ... and {len(portfolio['allocations']) - 5} more")

    # Save portfolio so paper_trader.py can pick it up later
    portfolio_path = os.path.join(config.REPORT_DIR, "latest_portfolio.json")
    os.makedirs(config.REPORT_DIR, exist_ok=True)
    with open(portfolio_path, "w") as f:
        json.dump(portfolio, f, indent=2, default=str)

    # ── Step 8: Generate dashboard ───────────────────────────────────
    print_step(8, "Generating HTML dashboard...")
    bots_for_dashboard = []
    for bd in bot_data_list:
        b = dict(bd["bot"])
        b["pair"] = b.get("pair", "")
        bots_for_dashboard.append(b)

    learning_stats = {"calibration": learner.state.get("calibration", {})}
    execution_summary = counts

    html = dashboard_gen.generate(bots_for_dashboard, evaluations, regime_info,
                                   learning_stats, execution_summary, portfolio=portfolio)
    path = dashboard_gen.save(html)
    print(f"    Dashboard saved to: {C.CYAN}{path}{C.RESET}")
    logger.info(f"Dashboard saved to {path}")

    # ── Step 8.5: Paper trading (optional) ───────────────────────────
    if args.paper_trade:
        print_step("8b", "Executing paper trades on Alpaca...")
        try:
            from paper_trader import PaperTrader, format_report
            trader = PaperTrader()
            acct = trader.client.get_account()
            print(f"    Alpaca paper account — Equity: ${acct['equity']:,.2f}, "
                  f"Cash: ${acct['cash']:,.2f}")
            results = trader.execute_portfolio(portfolio, dry_run=not args.execute)
            print(format_report(results))
        except (ValueError, ImportError) as e:
            print(f"    {C.RED}Paper trading unavailable: {e}{C.RESET}")
            print(f"    {C.YELLOW}Run: pip install alpaca-py{C.RESET}")
            print(f"    {C.YELLOW}Then set ALPACA_API_KEY and ALPACA_SECRET_KEY env vars.{C.RESET}")
        except Exception as e:
            print(f"    {C.RED}Paper trading error: {e}{C.RESET}")
            logger.error(f"Paper trading failed: {e}", exc_info=True)

    # ── Step 9: Print summary ────────────────────────────────────────
    print(f"\n{C.CYAN}{C.BOLD}" + "─" * 64 + C.RESET)
    print(f"  {C.BOLD}Summary{C.RESET}")
    print(f"  {C.CYAN}{'─' * 40}{C.RESET}")
    print(f"    Bots evaluated:    {C.BOLD}{len(evaluations)}{C.RESET}")
    print(f"    {C.RED}PAUSE:             {counts.get('PAUSE', 0)}{C.RESET}")
    print(f"    {C.YELLOW}HOLD:              {counts.get('HOLD', 0)}{C.RESET}")
    print(f"    {C.GREEN}REACTIVATE:        {counts.get('REACTIVATE', 0)}{C.RESET}")
    print(f"    {C.GRAY}INSUFFICIENT_DATA: {counts.get('INSUFFICIENT_DATA', 0)}{C.RESET}")
    print(f"    Market regime:     {regime_color}{regime.replace('_',' ').title()}{C.RESET}")
    if actions_taken:
        print(f"    {C.MAGENTA}Actions executed:  {len(actions_taken)}{C.RESET}")
        for a in actions_taken:
            print(f"      ⚡ {a['action']} → {a['bot']}")
    print(f"{C.CYAN}{C.BOLD}" + "─" * 64 + C.RESET)
    print(f"  {C.GREEN}Done!{C.RESET} Dashboard: {path}\n")

    # ── Step 10: Record last refresh timestamp ────────────────────
    _write_last_refresh(
        counts=counts,
        regime=regime,
        regime_confidence=regime_info.get("confidence", 0),
        expected_monthly_return_pct=portfolio["summary"].get("expected_monthly_return_pct", 0),
        num_strategies=portfolio["summary"]["num_strategies"],
        triggered_by=os.getenv("SF_TRIGGER", "manual"),
        actions_taken=len(actions_taken),
    )

    return evaluations


def _write_last_refresh(**meta):
    """Write a JSON file describing the most recent successful run."""
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        # Format for human display in Eastern time
        if ZoneInfo is not None:
            ny = now_utc.astimezone(ZoneInfo("America/New_York"))
            human_est = ny.strftime("%b %d, %Y %I:%M %p %Z")
        else:
            human_est = now_utc.strftime("%Y-%m-%d %H:%M UTC")
        payload = {
            "refreshed": True,
            "timestamp_utc": now_utc.isoformat(),
            "timestamp_iso": now_utc.isoformat(),
            "display_est": human_est,
            **meta,
        }
        path = os.path.join(config.DATA_DIR, "last_refresh.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, default=str)
    except Exception as e:
        logging.getLogger("strategy_factory").warning(f"Could not write last_refresh.json: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Strategy Factory — Daily Bot Review",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python daily_runner.py                          Dry run — analyze and report only
  python daily_runner.py --execute                Live mode — pause/reactivate bots
  python daily_runner.py --paper-trade            Preview Alpaca orders (dry run)
  python daily_runner.py --paper-trade --execute  Place real paper orders on Alpaca
  python daily_runner.py --dump-raw               Debug mode — dump raw API/DB data
  python daily_runner.py -v                       Verbose logging
        """
    )
    parser.add_argument("--execute", action="store_true",
                        help="Live mode: actually pause/reactivate bots and place paper orders")
    parser.add_argument("--dump-raw", action="store_true",
                        help="Debug mode: dump raw data and exit")
    parser.add_argument("--paper-trade", action="store_true",
                        help="After analysis, place paper orders on Alpaca to match portfolio")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable verbose/debug logging")
    args = parser.parse_args()

    logger = setup_logging(args.verbose)
    print_header()

    try:
        run_analysis(args, logger)
    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}Interrupted by user.{C.RESET}")
        sys.exit(0)
    except Exception as e:
        print(f"\n  {C.RED}Error: {e}{C.RESET}")
        if args.verbose:
            traceback.print_exc()
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
