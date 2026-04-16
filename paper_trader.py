"""
Strategy Factory — Paper Trader

Takes portfolio allocations produced by daily_runner.py and executes them as
simulated orders via PaperBroker (local simulator using synthetic math-based pricing).

Uses dashboard-configured starting capital (default $1,000). No external broker.
"""

import os
import json
import logging
import datetime

import config
from paper_broker import PaperBroker, normalize_symbol, SUPPORTED_SYMBOLS

logger = logging.getLogger("paper_trader")

PAPER_TRADE_HISTORY = os.path.join(config.DATA_DIR, "paper_trade_runs.json")
REBALANCE_THRESHOLD_PCT = 15.0  # only trade if position drifts >15% from target


def allocation_monthly_return_pct(alloc):
    allocation_usd = float(alloc.get("allocation_usd", 0) or 0)
    expected_monthly_return = float(alloc.get("expected_monthly_return", 0) or 0)
    if allocation_usd <= 0:
        return 0.0
    return (expected_monthly_return / allocation_usd) * 100.0


class PaperTrader:
    """Executes portfolio allocations via the local PaperBroker."""

    def __init__(self, starting_balance=1000.0):
        self.client = PaperBroker(starting_balance=starting_balance)
        self.runs = self._load_runs()

    def _load_runs(self):
        if os.path.exists(PAPER_TRADE_HISTORY):
            try:
                with open(PAPER_TRADE_HISTORY) as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_runs(self):
        os.makedirs(os.path.dirname(PAPER_TRADE_HISTORY), exist_ok=True)
        with open(PAPER_TRADE_HISTORY, "w") as f:
            json.dump(self.runs[-60:], f, indent=2, default=str)

    def get_account_summary(self):
        acct = self.client.get_account()
        positions = self.client.get_positions()
        total_unrealized_pl = sum(p["unrealized_pl"] for p in positions)
        return {
            "account": acct,
            "positions": positions,
            "total_unrealized_pl": round(total_unrealized_pl, 2),
            "position_count": len(positions),
        }

    def execute_portfolio(self, portfolio, dry_run=False, capital_override=None):
        """Open paper positions matching the portfolio allocations."""
        allocations = portfolio.get("allocations", [])
        if not allocations:
            return {"status": "no_allocations", "orders": []}

        acct = self.client.get_account()
        positions_list = self.client.get_positions()
        positions = {p["symbol"]: p for p in positions_list}

        # Scale by CURRENT EQUITY so profits get reinvested automatically.
        # Example: start $1,000 → grow to $1,500 → scale = 1.5x → each strategy gets 1.5x original allocation.
        dashboard_capital = portfolio.get("summary", {}).get("total_capital", 1000)
        effective_capital = capital_override or acct.get("equity", acct.get("cash", 1000))
        scale = effective_capital / dashboard_capital if dashboard_capital > 0 else 1.0

        logger.info(f"Account equity: ${acct.get('equity', 0):.2f} (starting ${acct.get('starting_balance', 0):.2f}), "
                    f"scale factor: {scale:.3f}x (profits will reinvest)")

        results = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "dry_run": dry_run,
            "account_cash_before": acct["cash"],
            "account_equity_before": acct["equity"],
            "starting_balance": acct.get("starting_balance", 0),
            "scale_factor": scale,
            "orders": [],
            "skipped": [],
            "summary": {},
        }

        target_by_symbol = {}

        for alloc in allocations:
            bot_name = alloc.get("bot_name", "?")
            pair = alloc.get("pair", "")
            dollar_alloc = alloc.get("allocation_usd", 0) * scale
            model_monthly_return_pct = allocation_monthly_return_pct(alloc)

            sym = normalize_symbol(pair)
            if not sym:
                results["skipped"].append({
                    "bot": bot_name, "pair": pair,
                    "reason": f"Symbol {pair} not supported by simulator."
                })
                continue

            if dollar_alloc < 1.0:
                results["skipped"].append({
                    "bot": bot_name, "pair": pair,
                    "reason": f"Allocation ${dollar_alloc:.2f} below $1 minimum"
                })
                continue

            target_by_symbol[sym] = {
                "bot_name": bot_name,
                "target_usd": round(dollar_alloc, 2),
                "allocation_pct": alloc.get("allocation_pct", 0),
            }

            existing = positions.get(sym)
            current_value = existing["market_value"] if existing else 0
            diff = dollar_alloc - current_value
            pct_diff = abs(diff) / dollar_alloc * 100 if dollar_alloc > 0 else 100

            if existing and pct_diff < REBALANCE_THRESHOLD_PCT:
                results["skipped"].append({
                    "bot": bot_name, "pair": sym,
                    "reason": f"Already allocated (${current_value:.2f} vs target ${dollar_alloc:.2f}, {pct_diff:.1f}% drift — below rebalance threshold)"
                })
                continue

            side = "buy" if diff > 0 else "sell"
            order_usd = abs(diff)

            if side == "buy":
                available_cash = float(self.client.state.get("cash", 0))
                order_usd = min(order_usd, available_cash)
                if order_usd < 1.0:
                    results["skipped"].append({
                        "bot": bot_name, "pair": sym,
                        "reason": f"Remaining cash ${available_cash:.2f} below $1 minimum"
                    })
                    continue

            if dry_run:
                results["orders"].append({
                    "bot": bot_name, "symbol": sym, "side": side,
                    "notional": round(order_usd, 2),
                    "status": "DRY_RUN",
                    "target_usd": dollar_alloc,
                    "current_usd": current_value,
                })
            else:
                order_result = self.client.submit_order(
                    sym,
                    order_usd,
                    side=side,
                    model_monthly_return_pct=model_monthly_return_pct if side == "buy" else None,
                )
                order_result["bot"] = bot_name
                order_result["target_usd"] = dollar_alloc
                order_result["current_usd"] = current_value
                results["orders"].append(order_result)

        # Close positions that dropped out of the plan
        for sym, pos in positions.items():
            if sym not in target_by_symbol:
                if dry_run:
                    results["orders"].append({
                        "symbol": sym, "side": "close",
                        "notional": pos["market_value"],
                        "status": "DRY_RUN_CLOSE",
                    "reason": "No longer in target portfolio",
                    })
                else:
                    close_result = self.client.close_position(sym)
                    close_result["reason"] = "No longer in target portfolio"
                    close_result["side"] = "close"
                    results["orders"].append(close_result)

        successful_orders = [o for o in results["orders"] if not o.get("error")]
        buys = sum(1 for o in successful_orders if o.get("side") == "buy")
        sells = sum(1 for o in successful_orders if o.get("side") == "sell")
        closes = sum(1 for o in successful_orders if o.get("side") == "close")
        total_deployed = sum(o.get("notional", 0) for o in successful_orders if o.get("side") == "buy")

        results["summary"] = {
            "total_orders": len(results["orders"]),
            "buys": buys,
            "sells": sells,
            "closes": closes,
            "skipped": len(results["skipped"]),
            "total_capital_deployed_usd": round(total_deployed, 2),
            "num_target_positions": len(target_by_symbol),
        }

        if not dry_run:
            self.runs.append({
                "timestamp": results["timestamp"],
                "summary": results["summary"],
                "account_equity_after": self.client.get_account()["equity"],
            })
            self._save_runs()

        return results


def format_report(results):
    out = []
    out.append("=" * 60)
    out.append(f"  📈 PAPER TRADING — {'DRY RUN' if results.get('dry_run') else 'EXECUTED'}")
    out.append(f"  {results.get('timestamp', '')}")
    out.append("=" * 60)
    out.append(f"  Starting balance: ${results.get('starting_balance', 0):,.2f}")
    out.append(f"  Cash before:      ${results.get('account_cash_before', 0):,.2f}")
    out.append(f"  Equity before:    ${results.get('account_equity_before', 0):,.2f}")
    out.append(f"  Scale factor:     {results.get('scale_factor', 1):.3f}x")
    out.append("")

    s = results.get("summary", {})
    out.append(f"  Orders: {s.get('total_orders', 0)}  "
               f"({s.get('buys', 0)} buys · {s.get('sells', 0)} sells · {s.get('closes', 0)} closes)")
    out.append(f"  Skipped: {s.get('skipped', 0)}")
    out.append(f"  Capital deployed: ${s.get('total_capital_deployed_usd', 0):,.2f}")
    out.append("")

    for o in results.get("orders", []):
        bot = o.get("bot", "—")
        sym = o.get("symbol", "?")
        side = o.get("side", "?")
        notional = o.get("notional", 0) or 0
        status = o.get("status", "?")
        err = o.get("error", "")
        line = f"    {side.upper():5s} ${notional:7.2f}  {sym:10s}  {bot:30s}  [{status}]"
        if err:
            line += f"  ❌ {err}"
        out.append(line)

    skipped = results.get("skipped", [])
    if skipped:
        out.append("")
        out.append("  Skipped:")
        for s in skipped[:10]:
            out.append(f"    • {s.get('bot', '?'):30s} {s.get('pair', ''):10s} {s.get('reason', '')}")
        if len(skipped) > 10:
            out.append(f"    ... and {len(skipped) - 10} more")

    out.append("=" * 60)
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    from config import REPORT_DIR

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(message)s")
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv

    portfolio_path = os.path.join(REPORT_DIR, "latest_portfolio.json")
    if not os.path.exists(portfolio_path):
        print("❌ No portfolio found. Run daily_runner.py first.")
        sys.exit(1)
    with open(portfolio_path) as f:
        portfolio = json.load(f)

    trader = PaperTrader()
    acct = trader.client.get_account()
    print(f"📊 Paper account — Equity ${acct['equity']:,.2f} · "
          f"Cash ${acct['cash']:,.2f} · Starting ${acct['starting_balance']:,.2f}")
    results = trader.execute_portfolio(portfolio, dry_run=dry_run)
    print(format_report(results))
