"""
Strategy Factory — Alpaca Portfolio Trader (Adaptive Breakout)

Executes Adaptive Breakout strategy signals on Alpaca paper/live trading.
Single strategy: Donchian breakout + ADX filter on 4h timeframe.

Exit logic:
  - Trailing stop: 3x ATR(14) from peak price
  - Hard stop: 8% loss from entry
  - ADX exit: close when ADX drops below 15

Concurrency: max 3 crypto + max 3 stock positions at any time.
Long only. Cooldown: 2 bars (8h) after a losing trade.
"""

import os
import json
import logging
import datetime

import config
from alpaca_client import AlpacaPaperClient, is_equity_symbol, is_us_market_open, normalize_crypto_symbol
from intraday_engine import IntradaySignalEngine, FeatureSet, MarketDataProvider, atr
from trade_journal import PositionRiskBook, TradeJournal

logger = logging.getLogger("alpaca_trader")

ALPACA_TRADE_HISTORY = os.path.join(config.DATA_DIR, "alpaca_trade_runs.json")
REBALANCE_THRESHOLD_PCT = 20.0
INTRADAY_GATE_ENABLED = os.environ.get("INTRADAY_GATE_ENABLED", "true").lower() != "false"

# Alpaca-supported crypto pairs
ALPACA_SUPPORTED_CRYPTO = {
    "BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD",
    "SHIB/USD", "UNI/USD", "LINK/USD", "LTC/USD",
    "BCH/USD", "AAVE/USD", "XRP/USD", "ADA/USD", "ALGO/USD",
    "ATOM/USD", "CRV/USD", "NEAR/USD", "MKR/USD", "GRT/USD",
    "SUSHI/USD", "YFI/USD", "BAT/USD", "XTZ/USD", "USDT/USD",
    "USDC/USD", "DAI/USD",
}

ALPACA_SUPPORTED_EQUITIES = {"TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META"}


def _normalize_alpaca_symbol(pair):
    """Convert bot pair format to an Alpaca tradable symbol."""
    if not pair:
        return None
    p = pair.upper().replace(" ", "")
    if p in ALPACA_SUPPORTED_EQUITIES:
        return p
    if p.endswith("/USDT"):
        base = p[:-5]
        return f"{base}/USD"
    if p.endswith("USDT") and "/" not in p:
        base = p[:-4]
        return f"{base}/USD"
    if p.endswith("USD") and "/" not in p:
        base = p[:-3]
        return f"{base}/USD"
    if "/" in p and p.endswith("/USD"):
        return p
    return None


def _is_supported_alpaca_symbol(symbol):
    return symbol in ALPACA_SUPPORTED_CRYPTO or symbol in ALPACA_SUPPORTED_EQUITIES


class AlpacaTrader:
    """Executes Adaptive Breakout trades via Alpaca."""

    def __init__(self):
        self.client = AlpacaPaperClient()
        self.runs = self._load_runs()
        self.journal = TradeJournal()
        self.risk_book = PositionRiskBook()
        self.signal_engine = IntradaySignalEngine()
        self._data_provider = MarketDataProvider()
        # Learning engine for real trade outcome tracking
        try:
            from learning_engine import LearningEngine
            self.learner = LearningEngine()
            self.learner.ingest_trade_ledger()
        except Exception:
            self.learner = None

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

    def _count_open_positions(self, positions):
        """Count current crypto vs stock positions for concurrency limits."""
        crypto_count = 0
        stock_count = 0
        for sym in positions:
            if is_equity_symbol(sym):
                stock_count += 1
            else:
                crypto_count += 1
        return crypto_count, stock_count

    def _backfill_risk_book(self, positions):
        """Auto-populate risk book for positions missing after redeploy."""
        if not positions:
            return
        backfilled = []
        for sym, pos in positions.items():
            if self.risk_book.get(sym):
                continue

            entry_price = float(pos.get("avg_entry_price", 0) or 0)
            cost_basis = float(pos.get("cost_basis", 0) or 0)
            if entry_price <= 0:
                continue

            # Conservative defaults for backfilled positions
            self.risk_book.register_entry(
                symbol=sym,
                strategy="adaptive_breakout",
                regime="unknown",
                confidence=0.5,
                entry_price=entry_price,
                notional=cost_basis or entry_price,
                stop_loss_pct=config.HARD_STOP_PCT,
                take_profit_pct=99.0,  # No fixed TP — we use trailing + ADX exit
                trailing_stop_pct=0.0,  # ATR trailing handled separately
                max_hold_hours=999,  # No timeout — ADX exit handles this
                reason="Auto-backfilled after risk book loss (redeploy recovery)",
                bot_names=[],
            )
            backfilled.append(sym)
            logger.warning(f"Risk book backfill: {sym} @ ${entry_price:.4f}")

        if backfilled:
            logger.info(f"Risk book backfilled {len(backfilled)} positions: {backfilled}")

    def _check_post_loss_cooldown(self, symbol):
        """Check if symbol is in cooldown after a losing trade.
        Cooldown = 2 bars = 8 hours."""
        try:
            from trade_journal import JOURNAL_FILE, _read_json
            events = list(reversed(_read_json(JOURNAL_FILE, [])))
            now = datetime.datetime.now(datetime.timezone.utc)
            cooldown_hours = config.POST_LOSS_COOLDOWN_BARS * 4  # 2 bars * 4h = 8h

            for ev in events:
                if ev.get("symbol") != symbol:
                    continue
                if ev.get("event") == "position_closed":
                    pl_pct = float(ev.get("unrealized_pl_pct", 0) or 0)
                    if pl_pct < 0:
                        ts = ev.get("timestamp", ev.get("closed_at", ""))
                        if not ts:
                            continue
                        try:
                            closed_at = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            hours_since = (now - closed_at).total_seconds() / 3600
                            if hours_since < cooldown_hours:
                                return True, f"Post-loss cooldown: closed at {pl_pct:.1f}% loss {hours_since:.1f}h ago (need {cooldown_hours}h)"
                        except Exception:
                            pass
                    break  # only check most recent close
        except Exception as e:
            logger.debug(f"Cooldown check failed for {symbol}: {e}")
        return False, ""

    def execute_portfolio(self, portfolio, dry_run=False, capital_override=None):
        """Open Alpaca positions matching the portfolio allocations."""
        allocations = portfolio.get("allocations", [])
        if not allocations:
            return {"status": "no_allocations", "orders": [],
                    "summary": {"buys": 0, "sells": 0, "closes": 0,
                                "total_orders": 0, "skipped": 0,
                                "total_capital_deployed_usd": 0,
                                "num_target_positions": 0}}

        acct = self.client.get_account()
        positions_list = self.client.get_positions()
        positions = {normalize_crypto_symbol(p["symbol"]): p for p in positions_list}

        # Apply risk controls
        try:
            from risk_manager import RiskManager
            rm = RiskManager()
            ok, reasons = rm.pre_trade_check(float(acct.get("equity", 0)))
            if not ok:
                return {"status": "risk_blocked", "reasons": reasons, "orders": []}
            cooldown_mult = rm.get_exposure_multiplier()
        except ImportError:
            cooldown_mult = 1.0
            rm = None
        except Exception as e:
            logger.warning(f"Risk manager unavailable: {e}")
            cooldown_mult = 1.0
            rm = None

        dashboard_capital = portfolio.get("summary", {}).get("total_capital", 1000)
        effective_capital = capital_override or acct.get("equity", acct.get("cash", 1000))
        scale = effective_capital / dashboard_capital if dashboard_capital > 0 else 1.0
        remaining_cash = float(acct.get("buying_power", acct.get("cash", 0)))

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

        # Backfill risk book after redeploy
        try:
            self._backfill_risk_book(positions)
        except Exception as e:
            logger.warning(f"Risk book backfill failed: {e}")

        # ── EXIT CHECK: ATR trailing + hard stop + ADX exit ──
        if not dry_run:
            try:
                exit_orders = self._enforce_adaptive_exits(positions)
                if exit_orders:
                    results["orders"].extend(exit_orders)
                    positions_list = self.client.get_positions()
                    positions = {normalize_crypto_symbol(p["symbol"]): p for p in positions_list}
            except Exception as e:
                logger.warning(f"Adaptive exit check failed: {e}")

        # Position stop losses from risk manager
        if rm:
            try:
                closed_stops = rm.enforce_position_stops(self.client)
                if closed_stops:
                    for cs in closed_stops:
                        results["orders"].append({"symbol": cs["symbol"], "side": "close", "status": "stop_loss", "loss_pct": cs["loss_pct"]})
                    positions_list = self.client.get_positions()
                    positions = {normalize_crypto_symbol(p["symbol"]): p for p in positions_list}
            except Exception:
                pass

        # Pre-filter: supported vs unsupported
        supported_allocs = []
        unsupported_allocs = []
        for alloc in allocations:
            pair = alloc.get("pair", "")
            sym = _normalize_alpaca_symbol(pair)
            if sym and _is_supported_alpaca_symbol(sym):
                supported_allocs.append((alloc, sym))
            else:
                results["skipped"].append({
                    "bot": alloc.get("bot_name", "?"), "pair": pair,
                    "reason": f"Symbol {pair} → {sym or '?'} not supported on Alpaca",
                })
                unsupported_allocs.append(alloc)

        # Redistribute unsupported capital
        unsupported_total = sum(a.get("allocation_usd", 0) for a in unsupported_allocs)
        supported_total = sum(a.get("allocation_usd", 0) for a, _ in supported_allocs)
        redistribution_factor = 1.0
        if unsupported_total > 0 and supported_total > 0:
            redistribution_factor = (supported_total + unsupported_total) / supported_total

        # Aggregate allocations by symbol
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
                target_by_symbol[sym]["target_usd"] += round(dollar_alloc, 2)
                target_by_symbol[sym]["allocation_pct"] += alloc.get("allocation_pct", 0)
                target_by_symbol[sym]["bot_names"].append(bot_name)
            else:
                target_by_symbol[sym] = {
                    "bot_names": [bot_name],
                    "target_usd": round(dollar_alloc, 2),
                    "allocation_pct": alloc.get("allocation_pct", 0),
                }

        # ── INTRADAY GATE: Adaptive Breakout signal required for new entries ──
        if INTRADAY_GATE_ENABLED:
            self._apply_intraday_gate(target_by_symbol, positions, results)

        # Apply exposure limits
        if rm:
            try:
                rm.apply_exposure_limits(target_by_symbol, float(acct.get("equity", effective_capital)))
                if cooldown_mult < 1.0:
                    for sym in target_by_symbol:
                        target_by_symbol[sym]["target_usd"] *= cooldown_mult
            except Exception:
                pass

        # ── CONCURRENCY CHECK: max 3 crypto + max 3 stocks ──
        crypto_count, stock_count = self._count_open_positions(positions)

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
                    "reason": f"Already allocated (${current_value:.2f} vs target ${dollar_alloc:.2f})"
                })
                continue

            # Trade frequency limit
            if rm:
                try:
                    if not rm.can_place_order(sym):
                        results["skipped"].append({"bot": label, "pair": sym, "reason": "Trade frequency limit"})
                        continue
                except Exception:
                    pass

            side = "buy" if diff > 0 else "sell"
            order_usd = abs(diff)

            if rm:
                try:
                    if not rm.can_submit_order(sym, side):
                        results["skipped"].append({"bot": label, "pair": sym, "reason": f"Duplicate {side} order blocked"})
                        continue
                except Exception:
                    pass

            # ── CONCURRENCY LIMIT for new buys ──
            if side == "buy" and not existing:
                is_stock = is_equity_symbol(sym)
                if is_stock and stock_count >= config.MAX_CONCURRENT_STOCKS:
                    results["skipped"].append({
                        "bot": label, "pair": sym,
                        "reason": f"Max {config.MAX_CONCURRENT_STOCKS} stock positions reached"
                    })
                    continue
                if not is_stock and crypto_count >= config.MAX_CONCURRENT_CRYPTO:
                    results["skipped"].append({
                        "bot": label, "pair": sym,
                        "reason": f"Max {config.MAX_CONCURRENT_CRYPTO} crypto positions reached"
                    })
                    continue

            if side == "buy":
                order_usd = min(order_usd, remaining_cash)
                if order_usd < 1.0:
                    results["skipped"].append({
                        "bot": label, "pair": sym,
                        "reason": f"Buying power ${remaining_cash:.2f} insufficient"
                    })
                    continue

            # Market hours guard for stocks
            if is_equity_symbol(sym) and not is_us_market_open():
                results["skipped"].append({
                    "bot": label, "pair": sym,
                    "reason": "US equity market closed — skipping stock order",
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
                    self._record_trade_event(order_result, target, side, order_usd)
                    if side == "buy":
                        remaining_cash -= order_usd
                        # Update concurrency count
                        if is_equity_symbol(sym):
                            stock_count += 1
                        else:
                            crypto_count += 1
                    if rm:
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
                if is_equity_symbol(sym) and not is_us_market_open():
                    results["skipped"].append({
                        "bot": sym, "pair": sym,
                        "reason": "US market closed — deferring equity close",
                    })
                    continue
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
                        self._record_close(sym, pos, entry_state, current_price, pl_pct, "No longer in target portfolio")
                    except Exception as e:
                        results["orders"].append({
                            "symbol": sym, "side": "close",
                            "status": "error", "error": str(e),
                            "reason": "No longer in target portfolio",
                        })

        successful_orders = [o for o in results["orders"] if not o.get("error")]
        buys = sum(1 for o in successful_orders if o.get("side") == "buy")
        sells = sum(1 for o in successful_orders if o.get("side") == "sell")
        closes = sum(1 for o in successful_orders if o.get("side") == "close")
        total_deployed = sum(o.get("notional", 0) for o in successful_orders if o.get("side") == "buy")

        results["summary"] = {
            "total_orders": len(results["orders"]),
            "buys": buys, "sells": sells, "closes": closes,
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
        """Gate new entries through the Adaptive Breakout signal engine."""
        for sym in list(target_by_symbol.keys()):
            target = target_by_symbol[sym]
            existing = positions.get(sym)

            # Post-loss cooldown for new entries
            if not existing:
                blocked, cooldown_reason = self._check_post_loss_cooldown(sym)
                if blocked:
                    results["skipped"].append({
                        "bot": f"{sym} ({len(target.get('bot_names', []))} bots)",
                        "pair": sym,
                        "reason": cooldown_reason,
                    })
                    del target_by_symbol[sym]
                    continue

            signal = self.signal_engine.evaluate_symbol(sym)
            results["signals"][sym] = signal
            target["signal"] = signal

            # ADX exit signal — close existing position
            if signal.get("adx_exit") and existing:
                target["target_usd"] = 0.0
                target["intraday_reason"] = signal.get("adx_exit_reason", "ADX exit")
                self.journal.append({
                    "event": "target_downweighted",
                    "symbol": sym,
                    "reason": signal.get("adx_exit_reason"),
                    "signal": signal,
                })
                continue

            # Strong sell signal — close existing
            if signal.get("action") == "sell" and signal.get("confidence", 0) >= 0.56:
                if existing:
                    target["target_usd"] = 0.0
                    target["intraday_reason"] = "Strong opposite signal"
                    continue
                else:
                    del target_by_symbol[sym]
                    continue

            # Entry signal accepted
            if signal.get("accepted") and signal.get("action") == "buy":
                confidence = float(signal.get("confidence", 0.0))
                adx_val = float((signal.get("features", {}) or {}).get("adx_14", 0) or 0)

                # Scale position by ADX strength
                # ADX 35+ = full size, ADX 25-35 = 85%, ADX 20-25 = 70%
                if adx_val >= 35:
                    adx_multiplier = 1.0
                elif adx_val >= 25:
                    adx_multiplier = 0.85
                else:
                    adx_multiplier = 0.70

                multiplier = max(0.5, min(1.15, 0.5 + confidence * 0.7)) * adx_multiplier
                target["target_usd"] = round(target["target_usd"] * multiplier, 2)
                target["intraday_reason"] = signal.get("reason", "")
                continue

            # No entry signal — handle existing positions
            if existing:
                current_value = float(existing.get("market_value", target["target_usd"]) or target["target_usd"])
                target["target_usd"] = current_value  # hold current position
                target["intraday_reason"] = f"Held existing position: {signal.get('reason')}"
            else:
                results["skipped"].append({
                    "bot": f"{sym} ({len(target.get('bot_names', []))} bots)",
                    "pair": sym,
                    "reason": f"No breakout signal: {signal.get('reason')}",
                })
                self.journal.append({
                    "event": "entry_rejected",
                    "symbol": sym,
                    "reason": signal.get("reason"),
                    "signal": signal,
                })
                del target_by_symbol[sym]

    def _enforce_adaptive_exits(self, positions):
        """Adaptive Breakout exit logic:
        1. Hard stop: 8% loss from entry
        2. ATR trailing stop: 3x ATR(14) from peak price
        3. ADX exit: ADX drops below 15 (trend dying)
        """
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

            reason = None

            # ── EXIT 1: Hard stop loss (8%) — non-negotiable ──
            if pl_pct <= -config.HARD_STOP_PCT:
                reason = f"Hard stop hit ({pl_pct:.2f}% loss, limit={config.HARD_STOP_PCT}%)"

            # ── EXIT 2: ATR trailing stop (3x ATR from peak) ──
            if not reason:
                try:
                    candles = self._data_provider.get_candles(sym, config.STRATEGY_TIMEFRAME, limit=20)
                    if candles and len(candles) >= 14:
                        atr_vals = atr(candles, 14)
                        if atr_vals and atr_vals[-1] > 0:
                            atr_trail_distance = atr_vals[-1] * config.ATR_TRAIL_MULTIPLIER
                            trail_stop_price = high_water - atr_trail_distance
                            if current_price <= trail_stop_price:
                                trail_pct = (high_water - current_price) / high_water * 100
                                reason = (
                                    f"ATR trailing stop: price {current_price:.2f} below "
                                    f"trail {trail_stop_price:.2f} (peak {high_water:.2f} - "
                                    f"{config.ATR_TRAIL_MULTIPLIER}x ATR {atr_vals[-1]:.2f}), "
                                    f"P/L={pl_pct:+.2f}%"
                                )
                except Exception as e:
                    logger.debug(f"ATR trailing check failed for {sym}: {e}")

            # ── EXIT 3: ADX exit (trend dying) ──
            if not reason:
                try:
                    signal = self.signal_engine.evaluate_symbol(sym)
                    if signal.get("adx_exit"):
                        reason = signal.get("adx_exit_reason", "ADX dropped below exit threshold")
                except Exception as e:
                    logger.debug(f"ADX exit check failed for {sym}: {e}")

            if reason:
                # Don't close stocks outside market hours
                if is_equity_symbol(sym) and not is_us_market_open():
                    logger.info(f"Exit deferred for {sym}: {reason} (market closed)")
                    continue

                close_result = self.client.close_position(sym)
                close_result["reason"] = reason
                close_result["side"] = "close"
                orders.append(close_result)

                self._record_close(sym, pos, state, current_price, pl_pct, reason)

        return orders

    def _record_trade_event(self, order_result, target, side, order_usd):
        """Record a new entry in the trade journal and risk book."""
        sym = order_result.get("symbol")
        signal = target.get("signal", {})
        event = {
            "event": "order_submitted",
            "symbol": sym,
            "side": side,
            "notional": round(order_usd, 2),
            "status": order_result.get("status"),
            "bot_names": target.get("bot_names", []),
            "strategy": "adaptive_breakout",
            "regime": (signal.get("trade_regime") or {}).get("label"),
            "confidence": signal.get("confidence"),
            "entry_reason": target.get("intraday_reason") or signal.get("reason"),
            "order": order_result,
        }
        self.journal.append(event)

        if side == "buy" and not order_result.get("error"):
            entry_price = order_result.get("filled_avg_price") or self.client.get_latest_price(sym)
            entry_notional = order_usd
            try:
                position = self.client.get_position(sym)
                if position:
                    entry_price = position.get("avg_entry_price") or entry_price
                    entry_notional = position.get("cost_basis") or entry_notional
            except Exception:
                pass

            self.risk_book.register_entry(
                symbol=sym,
                strategy="adaptive_breakout",
                regime=event["regime"],
                confidence=event["confidence"],
                entry_price=entry_price,
                notional=entry_notional,
                stop_loss_pct=config.HARD_STOP_PCT,
                take_profit_pct=99.0,  # No fixed TP
                trailing_stop_pct=0.0,  # ATR trailing handled in _enforce_adaptive_exits
                max_hold_hours=999,  # No timeout — ADX exit handles this
                reason=event["entry_reason"],
                bot_names=target.get("bot_names", []),
            )
        elif side in ("sell", "close") and not order_result.get("error"):
            self.risk_book.remove(sym)

    def _record_close(self, sym, pos, entry_state, current_price, pl_pct, reason):
        """Record a position close in journal, learning engine, and risk book."""
        close_entry_state = dict(entry_state) if entry_state else {}
        close_entry_state["entry_notional"] = float(
            pos.get("cost_basis", 0) or (entry_state or {}).get("entry_notional", 0) or 0
        )
        close_entry_state["entry_price"] = float(
            pos.get("avg_entry_price", 0) or (entry_state or {}).get("entry_price", 0) or 0
        )

        # Record real trade outcome in learning engine
        if self.learner:
            try:
                entry_notional = float(close_entry_state.get("entry_notional", 0) or 0)
                exit_notional = float(pos.get("market_value", 0) or 0)
                gross_pl = exit_notional - entry_notional
                if is_equity_symbol(sym):
                    total_fees = 0.0
                else:
                    from trade_journal import ALPACA_CRYPTO_TAKER_FEE_BPS
                    fee_pct = (ALPACA_CRYPTO_TAKER_FEE_BPS * 2) / 10000.0
                    total_fees = (entry_notional + exit_notional) * fee_pct / 2
                net_pl = gross_pl - total_fees
                strategy = (entry_state or {}).get("strategy", "adaptive_breakout")
                regime = (entry_state or {}).get("regime", "unknown")
                self.learner.record_real_trade(strategy, regime, net_pl, sym)
            except Exception as e:
                logger.debug(f"Learning engine record failed: {e}")

        self.risk_book.remove(sym)
        self.journal.append({
            "event": "position_closed",
            "symbol": sym,
            "side": "close",
            "reason": reason,
            "entry_state": close_entry_state,
            "exit_price": current_price,
            "exit_notional": pos.get("market_value"),
            "unrealized_pl_pct": round(pl_pct, 2),
        })
