"""
Strategy Factory — Alpaca Portfolio Trader

Takes portfolio allocations produced by daily_runner.py and executes them as
real orders on Alpaca paper trading. Same rebalancing logic as paper_trader.py
but hits the live Alpaca API instead of the local simulator.

Uses account equity for position sizing so profits get reinvested automatically.
"""

import os
import json
import logging
import datetime

import config
from alpaca_client import AlpacaPaperClient

logger = logging.getLogger("alpaca_trader")

ALPACA_TRADE_HISTORY = os.path.join(config.DATA_DIR, "alpaca_trade_runs.json")
REBALANCE_THRESHOLD_PCT = 15.0  # only trade if position drifts >15% from target


def _normalize_alpaca_symbol(pair):
    """Convert bot pair format to Alpaca crypto symbol format.

    Alpaca uses 'BTC/USD' style for crypto. Our bots use 'BTCUSDT' or 'BTC/USDT'.
    """
    if not pair:
        return None
    p = pair.upper().replace(" ", "")
    # Handle BTCUSDT → BTC/USD
    if p.endswith("USDT"):
        base = p[:-4]
        return f"{base}/USD"
    # Handle BTC/USDT → BTC/USD
    if p.endswith("/USDT"):
        base = p[:-5]
        return f"{base}/USD"
    # Handle BTCUSD → BTC/USD
    if p.endswith("USD") and "/" not in p:
        base = p[:-3]
        return f"{base}/USD"
    # Already in BTC/USD format
    if "/" in p and p.endswith("/USD"):
        return p
    return None


class AlpacaTrader:
    """Executes portfolio allocations via the Alpaca paper trading API."""

    def __init__(self):
        self.client = AlpacaPaperClient()
        self.runs = self._load_runs()

    def _load_runs(self):
        if os.path.exists(ALPACA_TRADE_HISTORY):
            try:
                with open(ALPACA_TRADE_HISTORY) as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_runs(self):
        os.makedirs(os.path.dirname(ALPACA_TRADE_HISTORY), exist_ok=True)
        with open(ALPACA_TRADE_HISTORY, "w") as f:
            json.dump(self.runs[-60:], f, indent=2, default=str)

    def execute_portfolio(self, portfolio, dry_run=False, capital_override=None):
        """Open Alpaca positions matching the portfolio allocations."""
        allocations = portfolio.get("allocations", [])
        if not allocations:
            return {"status": "no_allocations", "orders": []}

        acct = self.client.get_account()
        positions_list = self.client.get_positions()
        positions = {p["symbol"]: p for p in positions_list}

        # Scale by CURRENT EQUITY so profits get reinvested
        dashboard_capital = portfolio.get("summary", {}).get("total_capital", 1000)
        effective_capital = capital_override or acct.get("equity", acct.get("cash", 1000))
        scale = effective_capital / dashboard_capital if dashboard_capital > 0 else 1.0

        logger.info(f"Alpaca equity: ${acct.get('equity', 0):.2f}, "
                    f"scale factor: {scale:.3f}x")

        results = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "dry_run": dry_run,
            "broker": "alpaca",
            "account_cash_before": acct["cash"],
            "account_equity_before": acct["equity"],
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

            sym = _normalize_alpaca_symbol(pair)
            if not sym:
                results["skipped"].append({
                    "bot": bot_name, "pair": pair,
                    "reason": f"Symbol {pair} not supported on Alpaca"
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
                    "reason": f"Already allocated (${current_value:.2f} vs target ${dollar_alloc:.2f}, "
                              f"{pct_diff:.1f}% drift — below threshold)"
                })
                continue

            side = "buy" if diff > 0 else "sell"
            order_usd = abs(diff)

            if side == "buy":
                available_cash = float(acct.get("buying_power", acct.get("cash", 0)))
                order_usd = min(order_usd, available_cash)
                if order_usd < 1.0:
                    results["skipped"].append({
                        "bot": bot_name, "pair": sym,
                        "reason": f"Buying power ${available_cash:.2f} insufficient"
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
                try:
                    order_result = self.client.submit_order(sym, order_usd, side=side)
                    order_result["bot"] = bot_name
                    order_result["target_usd"] = dollar_alloc
                    order_result["current_usd"] = current_value
                    results["orders"].append(order_result)
                except Exception as e:
                    results["orders"].append({
                        "bot": bot_name, "symbol": sym, "side": side,
                        "notional": round(order_usd, 2),
                        "status": "error",
                        "error": str(e),
                    })

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
                    try:
                        close_result = self.client.close_position(sym)
                        close_result["reason"] = "No longer in target portfolio"
                        close_result["side"] = "close"
                        results["orders"].append(close_result)
                    except Exception as e:
                        results["orders"].append({
                            "symbol": sym, "side": "close",
                            "status": "error",
                            "error": str(e),
                            "reason": "No longer in target portfolio",
                        })

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
