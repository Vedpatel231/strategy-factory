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
from alpaca_client import AlpacaPaperClient, normalize_crypto_symbol
from risk_manager import RiskManager
from intraday_engine import IntradaySignalEngine
from trade_journal import PositionRiskBook, TradeJournal

logger = logging.getLogger("alpaca_trader")

ALPACA_TRADE_HISTORY = os.path.join(config.DATA_DIR, "alpaca_trade_runs.json")
REBALANCE_THRESHOLD_PCT = 20.0  # only trade if position drifts >20% from target
INTRADAY_GATE_ENABLED = os.environ.get("INTRADAY_GATE_ENABLED", "true").lower() != "false"
MAX_HOLD_HOURS = float(os.environ.get("INTRADAY_MAX_HOLD_HOURS", "18"))
BASE_STOP_LOSS_PCT = float(os.environ.get("INTRADAY_BASE_STOP_LOSS_PCT", "3.5"))
BASE_TAKE_PROFIT_PCT = float(os.environ.get("INTRADAY_BASE_TAKE_PROFIT_PCT", "6.0"))
BASE_TRAILING_STOP_PCT = float(os.environ.get("INTRADAY_BASE_TRAILING_STOP_PCT", "2.5"))

# Alpaca-supported crypto pairs (as of 2024). Checked via Alpaca API.
# If a portfolio symbol isn't in this set, its allocation gets redistributed.
ALPACA_SUPPORTED_CRYPTO = {
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "DOGE/USD",
    "SHIB/USD", "DOT/USD", "UNI/USD", "LINK/USD", "LTC/USD",
    "BCH/USD", "AAVE/USD", "XRP/USD", "ADA/USD", "ALGO/USD",
    "ATOM/USD", "CRV/USD", "NEAR/USD", "MKR/USD", "GRT/USD",
    "SUSHI/USD", "YFI/USD", "BAT/USD", "XTZ/USD", "USDT/USD",
    "USDC/USD", "DAI/USD",
}


def _normalize_alpaca_symbol(pair):
    """Convert bot pair format to Alpaca crypto symbol format.

    Alpaca uses 'BTC/USD' style for crypto. Our bots use 'BTCUSDT' or 'BTC/USDT'.
    """
    if not pair:
        return None
    p = pair.upper().replace(" ", "")
    # Handle BTC/USDT → BTC/USD (check slash version FIRST)
    if p.endswith("/USDT"):
        base = p[:-5]
        return f"{base}/USD"
    # Handle BTCUSDT → BTC/USD (no slash)
    if p.endswith("USDT") and "/" not in p:
        base = p[:-4]
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
        self.journal = TradeJournal()
        self.risk_book = PositionRiskBook()
        self.signal_engine = IntradaySignalEngine()

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
        positions = {normalize_crypto_symbol(p["symbol"]): p for p in positions_list}

        # Apply risk controls
        try:
            rm = RiskManager()
            ok, reasons = rm.pre_trade_check(float(acct.get("equity", 0)))
            if not ok:
                return {"status": "risk_blocked", "reasons": reasons, "orders": []}

            # Get exposure multiplier (cooldown)
            cooldown_mult = rm.get_exposure_multiplier()
            if cooldown_mult < 1.0:
                logger.info(f"Cooldown active: exposure multiplier {cooldown_mult}")
        except ImportError:
            cooldown_mult = 1.0
        except Exception as e:
            logger.warning(f"Risk manager unavailable: {e}")
            cooldown_mult = 1.0

        # Scale by CURRENT EQUITY so profits get reinvested
        dashboard_capital = portfolio.get("summary", {}).get("total_capital", 1000)
        effective_capital = capital_override or acct.get("equity", acct.get("cash", 1000))
        scale = effective_capital / dashboard_capital if dashboard_capital > 0 else 1.0

        remaining_cash = float(acct.get("buying_power", acct.get("cash", 0)))
        logger.info(f"Alpaca equity: ${acct.get('equity', 0):.2f}, "
                    f"buying power: ${remaining_cash:.2f}, scale factor: {scale:.3f}x")

        results = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "dry_run": dry_run,
            "broker": "alpaca",
            "account_cash_before": acct["cash"],
            "account_equity_before": acct["equity"],
            "scale_factor": scale,
            "orders": [],
            "skipped": [],
            "signals": {},
            "intraday_gate_enabled": INTRADAY_GATE_ENABLED,
            "summary": {},
        }

        # Soft exits (TP/SL/trailing/timeout) before new entries or rebalances.
        if not dry_run:
            try:
                exit_orders = self._enforce_intraday_exits(positions)
                if exit_orders:
                    results["orders"].extend(exit_orders)
                    positions_list = self.client.get_positions()
                    positions = {normalize_crypto_symbol(p["symbol"]): p for p in positions_list}
            except Exception as e:
                logger.warning(f"Intraday exit check failed: {e}")

        # Position stop losses
        try:
            closed_stops = rm.enforce_position_stops(self.client)
            if closed_stops:
                for cs in closed_stops:
                    results["orders"].append({"symbol": cs["symbol"], "side": "close", "status": "stop_loss", "loss_pct": cs["loss_pct"]})
                # Re-fetch positions after stop-loss closures
                positions_list = self.client.get_positions()
                positions = {normalize_crypto_symbol(p["symbol"]): p for p in positions_list}
        except Exception:
            pass

        # --- Pre-filter: identify supported vs unsupported allocations ---
        supported_allocs = []
        unsupported_allocs = []
        for alloc in allocations:
            pair = alloc.get("pair", "")
            sym = _normalize_alpaca_symbol(pair)
            if sym and sym in ALPACA_SUPPORTED_CRYPTO:
                supported_allocs.append((alloc, sym))
            else:
                bot_name = alloc.get("bot_name", "?")
                reason = f"Symbol {pair} → {sym or '?'} not supported on Alpaca"
                results["skipped"].append({
                    "bot": bot_name, "pair": pair, "reason": reason,
                })
                unsupported_allocs.append(alloc)

        # Redistribute unsupported capital proportionally to supported allocations
        unsupported_total = sum(a.get("allocation_usd", 0) for a in unsupported_allocs)
        supported_total = sum(a.get("allocation_usd", 0) for a, _ in supported_allocs)
        redistribution_factor = 1.0
        if unsupported_total > 0 and supported_total > 0:
            redistribution_factor = (supported_total + unsupported_total) / supported_total
            logger.info(f"Redistributing ${unsupported_total:.2f} from {len(unsupported_allocs)} "
                        f"unsupported symbols (factor {redistribution_factor:.3f}x)")

        # --- Aggregate allocations: multiple bots can target the same symbol ---
        target_by_symbol = {}

        for alloc, sym in supported_allocs:
            bot_name = alloc.get("bot_name", "?")
            dollar_alloc = alloc.get("allocation_usd", 0) * redistribution_factor * scale

            if dollar_alloc < 1.0:
                results["skipped"].append({
                    "bot": bot_name, "pair": sym,
                    "reason": f"Allocation ${dollar_alloc:.2f} below $1 minimum"
                })
                continue

            if sym in target_by_symbol:
                # Accumulate: add this bot's allocation to existing target
                target_by_symbol[sym]["target_usd"] += round(dollar_alloc, 2)
                target_by_symbol[sym]["allocation_pct"] += alloc.get("allocation_pct", 0)
                target_by_symbol[sym]["bot_names"].append(bot_name)
            else:
                target_by_symbol[sym] = {
                    "bot_names": [bot_name],
                    "target_usd": round(dollar_alloc, 2),
                    "allocation_pct": alloc.get("allocation_pct", 0),
                }

        logger.info(f"Aggregated {len(supported_allocs)} bot allocations into "
                    f"{len(target_by_symbol)} unique symbols")

        # Intraday quality gate: keep the allocator, but require a live setup for
        # new long exposure. Existing positions can be held if there is no strong
        # opposite signal, which avoids solving risk by simply never trading.
        if INTRADAY_GATE_ENABLED:
            self._apply_intraday_gate(target_by_symbol, positions, results)

        # Apply exposure limits
        try:
            rm.apply_exposure_limits(target_by_symbol, float(acct.get("equity", effective_capital)))
            # Apply cooldown multiplier
            if cooldown_mult < 1.0:
                for sym in target_by_symbol:
                    target_by_symbol[sym]["target_usd"] *= cooldown_mult
        except Exception:
            pass

        # --- Execute trades per symbol (not per bot) ---
        for sym, target in target_by_symbol.items():
            dollar_alloc = target["target_usd"]
            if dollar_alloc <= 0:
                dollar_alloc = 0
            label = f"{sym} ({len(target['bot_names'])} bots)"

            existing = positions.get(sym)
            current_value = existing["market_value"] if existing else 0
            diff = dollar_alloc - current_value
            pct_diff = abs(diff) / dollar_alloc * 100 if dollar_alloc > 0 else 100

            if existing and pct_diff < REBALANCE_THRESHOLD_PCT:
                results["skipped"].append({
                    "bot": label, "pair": sym,
                    "reason": f"Already allocated (${current_value:.2f} vs target ${dollar_alloc:.2f}, "
                              f"{pct_diff:.1f}% drift — below threshold)"
                })
                continue

            # Check trade frequency limit
            try:
                if not rm.can_place_order(sym):
                    results["skipped"].append({"bot": label, "pair": sym, "reason": "Trade frequency limit reached"})
                    continue
            except Exception:
                pass

            side = "buy" if diff > 0 else "sell"
            order_usd = abs(diff)

            try:
                if not rm.can_submit_order(sym, side):
                    results["skipped"].append({"bot": label, "pair": sym, "reason": f"Duplicate {side} order blocked"})
                    continue
            except Exception:
                pass

            if side == "buy":
                order_usd = min(order_usd, remaining_cash)
                if order_usd < 1.0:
                    results["skipped"].append({
                        "bot": label, "pair": sym,
                        "reason": f"Buying power ${remaining_cash:.2f} insufficient"
                    })
                    continue

            if dry_run:
                results["orders"].append({
                    "bot": label, "symbol": sym, "side": side,
                    "notional": round(order_usd, 2),
                    "status": "DRY_RUN",
                    "target_usd": dollar_alloc,
                    "current_usd": current_value,
                })
                if side == "buy":
                    remaining_cash -= order_usd
            else:
                try:
                    order_result = self.client.submit_order(sym, order_usd, side=side)
                    order_result["bot"] = label
                    order_result["target_usd"] = dollar_alloc
                    order_result["current_usd"] = current_value
                    results["orders"].append(order_result)
                    self._record_trade_event(order_result, target, side, order_usd, results)
                    if side == "buy":
                        remaining_cash -= order_usd
                    try:
                        rm.record_order(sym)
                        rm.record_submitted_order(sym, side)
                    except Exception:
                        pass
                except Exception as e:
                    results["orders"].append({
                        "bot": label, "symbol": sym, "side": side,
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
                        entry_state = self.risk_book.get(sym)
                        entry_price = float((entry_state or {}).get("entry_price") or pos.get("avg_entry_price") or 0)
                        current_price = float(pos.get("current_price", 0) or 0)
                        pl_pct = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 and current_price > 0 else 0.0
                        close_result = self.client.close_position(sym)
                        close_result["reason"] = "No longer in target portfolio"
                        close_result["side"] = "close"
                        results["orders"].append(close_result)
                        self.risk_book.remove(sym)
                        self.journal.append({
                            "event": "position_closed",
                            "symbol": sym,
                            "side": "close",
                            "reason": "No longer in target portfolio",
                            "entry_state": entry_state,
                            "exit_price": current_price,
                            "exit_notional": pos.get("market_value"),
                            "unrealized_pl_pct": round(pl_pct, 2),
                            "order": close_result,
                        })
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

    def _apply_intraday_gate(self, target_by_symbol, positions, results):
        for sym in list(target_by_symbol.keys()):
            target = target_by_symbol[sym]
            existing = positions.get(sym)
            signal = self.signal_engine.evaluate_symbol(sym)
            results["signals"][sym] = signal
            target["signal"] = signal

            if signal.get("action") == "sell" and signal.get("confidence", 0) >= 0.56:
                if existing:
                    target["target_usd"] = 0.0
                    target["intraday_reason"] = "Strong opposite intraday signal"
                    self.journal.append({
                        "event": "target_downweighted",
                        "symbol": sym,
                        "reason": "Strong opposite intraday signal",
                        "signal": signal,
                    })
                else:
                    del target_by_symbol[sym]
                continue

            if signal.get("accepted") and signal.get("action") == "buy":
                confidence = float(signal.get("confidence", 0.0))
                multiplier = max(0.55, min(1.25, 0.55 + confidence * 0.8))
                target["target_usd"] = round(target["target_usd"] * multiplier, 2)
                target["intraday_reason"] = signal.get("reason", "")
                continue

            if existing:
                # No fresh long setup: hold existing exposure without churn unless
                # normal risk controls or exit rules fire.
                target["target_usd"] = existing.get("market_value", target["target_usd"])
                target["intraday_reason"] = f"Held existing position: {signal.get('reason')}"
            else:
                results["skipped"].append({
                    "bot": f"{sym} ({len(target.get('bot_names', []))} bots)",
                    "pair": sym,
                    "reason": f"Intraday gate rejected new entry: {signal.get('reason')}",
                })
                self.journal.append({
                    "event": "entry_rejected",
                    "symbol": sym,
                    "reason": signal.get("reason"),
                    "signal": signal,
                    "bot_names": target.get("bot_names", []),
                })
                del target_by_symbol[sym]

    def _risk_params(self, signal):
        features = signal.get("features", {}) if isinstance(signal, dict) else {}
        atr_pct = float(features.get("atr_pct_15m", 0.0) or 0.0)
        confidence = float(signal.get("confidence", 0.0) or 0.0) if isinstance(signal, dict) else 0.0
        stop = max(BASE_STOP_LOSS_PCT, min(7.0, atr_pct * 1.6 if atr_pct else BASE_STOP_LOSS_PCT))
        take = max(BASE_TAKE_PROFIT_PCT, stop * (1.6 + confidence * 0.7))
        trail = max(BASE_TRAILING_STOP_PCT, min(5.0, stop * 0.7))
        return round(stop, 2), round(take, 2), round(trail, 2)

    def _record_trade_event(self, order_result, target, side, order_usd, results):
        sym = order_result.get("symbol")
        signal = target.get("signal", {})
        event = {
            "event": "order_submitted",
            "symbol": sym,
            "side": side,
            "notional": round(order_usd, 2),
            "status": order_result.get("status"),
            "bot_names": target.get("bot_names", []),
            "strategy": self._top_strategy(signal),
            "regime": signal.get("setup_regime", {}).get("label"),
            "confidence": signal.get("confidence"),
            "entry_reason": target.get("intraday_reason") or signal.get("reason"),
            "order": order_result,
        }
        self.journal.append(event)

        if side == "buy" and not order_result.get("error"):
            entry_price = order_result.get("filled_avg_price") or self.client.get_latest_price(sym)
            stop, take, trail = self._risk_params(signal)
            self.risk_book.register_entry(
                symbol=sym,
                strategy=event["strategy"],
                regime=event["regime"],
                confidence=event["confidence"],
                entry_price=entry_price,
                notional=order_usd,
                stop_loss_pct=stop,
                take_profit_pct=take,
                trailing_stop_pct=trail,
                max_hold_hours=MAX_HOLD_HOURS,
                reason=event["entry_reason"],
                bot_names=target.get("bot_names", []),
            )
        elif side in ("sell", "close") and not order_result.get("error"):
            self.risk_book.remove(sym)

    def _top_strategy(self, signal):
        strategies = signal.get("strategy_signals", []) if isinstance(signal, dict) else []
        if not strategies:
            return "portfolio_rebalance"
        best = sorted(
            strategies,
            key=lambda s: float(s.get("confidence", 0) or 0) * float(s.get("regime_fit", 1) or 1),
            reverse=True,
        )[0]
        return best.get("strategy", "unknown")

    def _enforce_intraday_exits(self, positions):
        orders = []
        now = datetime.datetime.now(datetime.timezone.utc)
        for sym, pos in positions.items():
            state = self.risk_book.get(sym)
            if not state:
                continue

            current_price = float(pos.get("current_price", 0) or 0)
            entry_price = float(state.get("entry_price") or pos.get("avg_entry_price") or 0)
            if current_price <= 0 or entry_price <= 0:
                continue

            self.risk_book.update_high_water(sym, current_price)
            state = self.risk_book.get(sym) or state
            high_water = float(state.get("high_water_price", current_price) or current_price)
            pl_pct = (current_price - entry_price) / entry_price * 100.0
            trail_dd = (high_water - current_price) / high_water * 100.0 if high_water > 0 else 0.0

            reason = None
            if pl_pct <= -float(state.get("stop_loss_pct", BASE_STOP_LOSS_PCT)):
                reason = f"Stop loss hit ({pl_pct:.2f}%)"
            elif pl_pct >= float(state.get("take_profit_pct", BASE_TAKE_PROFIT_PCT)):
                reason = f"Take profit hit ({pl_pct:.2f}%)"
            elif trail_dd >= float(state.get("trailing_stop_pct", BASE_TRAILING_STOP_PCT)) and pl_pct > 0:
                reason = f"Trailing stop hit ({trail_dd:.2f}% from high)"
            else:
                opened_at = state.get("opened_at", "")
                try:
                    opened = datetime.datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                    age_hours = (now - opened).total_seconds() / 3600
                    if age_hours >= float(state.get("max_hold_hours", MAX_HOLD_HOURS)) and pl_pct <= 0:
                        reason = f"Timeout exit after {age_hours:.1f}h without profit"
                except Exception:
                    pass

            if reason:
                close_result = self.client.close_position(sym)
                close_result["reason"] = reason
                close_result["side"] = "close"
                orders.append(close_result)
                self.risk_book.remove(sym)
                self.journal.append({
                    "event": "position_closed",
                    "symbol": sym,
                    "side": "close",
                    "reason": reason,
                    "entry_state": state,
                    "exit_price": current_price,
                    "exit_notional": pos.get("market_value"),
                    "unrealized_pl_pct": round(pl_pct, 2),
                    "order": close_result,
                })
        return orders
