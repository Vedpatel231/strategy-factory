"""Exit lifecycle manager for professional desk positions."""

from datetime import datetime, timezone

import config
from alpaca_client import AlpacaPaperClient, is_configured, is_equity_symbol, is_us_market_open
from decision_logger import DecisionLogger
from intraday_engine import MarketDataProvider, atr
from trade_journal import PositionRiskBook, TradeJournal


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


class ExitManager:
    def __init__(self, client=None, data_provider=None, risk_book=None, journal=None, logger=None):
        self.client = client
        self.data = data_provider or MarketDataProvider()
        self.risk_book = risk_book or PositionRiskBook()
        self.journal = journal or TradeJournal()
        self.logger = logger or DecisionLogger()

    def check_exits(self, positions=None, dry_run=False):
        if positions is None:
            if self.client is None:
                if not is_configured():
                    return {"checked": 0, "actions": [], "note": "Alpaca not configured; exits skipped."}
                self.client = AlpacaPaperClient()
            try:
                positions = self.client.get_positions(live_prices=True)
            except Exception as exc:
                return {"checked": 0, "actions": [], "error": str(exc)}

        actions = []
        for pos in positions or []:
            action = self._check_position(pos, dry_run=dry_run)
            if action:
                actions.append(action)
        return {"checked": len(positions or []), "actions": actions}

    def _check_position(self, pos, dry_run=False):
        symbol = pos.get("symbol")
        state = self.risk_book.get(symbol)
        if not state:
            return None
        current_price = _safe_float(pos.get("current_price"))
        entry_price = _safe_float(state.get("entry_price") or pos.get("avg_entry_price"))
        market_value = _safe_float(pos.get("market_value"))
        if current_price <= 0 or entry_price <= 0:
            return None

        self.risk_book.update_high_water(symbol, current_price)
        state = self.risk_book.get(symbol) or state
        high_water = _safe_float(state.get("high_water_price"), current_price)
        stop_price = _safe_float(state.get("stop_loss_price")) or entry_price * (1 - _safe_float(state.get("stop_loss_pct")) / 100.0)
        take_price = _safe_float(state.get("take_profit_price")) or entry_price * (1 + _safe_float(state.get("take_profit_pct")) / 100.0)
        partial_price = _safe_float(state.get("partial_profit_price"))
        pl_pct = (current_price - entry_price) / entry_price * 100.0

        reason = None
        close_all = False
        partial = False

        if stop_price > 0 and current_price <= stop_price:
            reason = f"Stop loss hit: price {current_price:.4f} <= stop {stop_price:.4f}."
            close_all = True
        elif take_price > 0 and current_price >= take_price:
            reason = f"Take profit hit: price {current_price:.4f} >= target {take_price:.4f}."
            close_all = True
        elif partial_price > 0 and not state.get("partial_profit_taken") and current_price >= partial_price:
            reason = f"Partial profit hit: price {current_price:.4f} >= partial target {partial_price:.4f}."
            partial = True
        else:
            trail_reason = self._trailing_reason(symbol, state, current_price, high_water, partial_price)
            if trail_reason:
                reason = trail_reason
                close_all = True
            else:
                invalid_reason = self._invalidation_reason(symbol, state, current_price)
                if invalid_reason:
                    reason = invalid_reason
                    close_all = True

        if not reason:
            return None

        if is_equity_symbol(symbol) and not is_us_market_open():
            event = {
                "timestamp": _utcnow(),
                "symbol": symbol,
                "action": "exit_deferred",
                "reason": f"{reason} US market is closed.",
            }
            self.logger.append("exit_deferred", event)
            return event

        if partial:
            notional = max(1.0, market_value * 0.50)
            result = self._submit_or_dry(symbol, notional, "sell", dry_run)
            if not result.get("error"):
                self.risk_book.update_fields(symbol, partial_profit_taken=True, trailing_active=True)
            event_type = "partial_profit"
        elif close_all:
            result = self._close_or_dry(symbol, dry_run)
            if not result.get("error"):
                self.risk_book.remove(symbol)
            event_type = "position_closed"
        else:
            return None

        event = {
            "event": event_type,
            "timestamp": _utcnow(),
            "symbol": symbol,
            "side": "close" if close_all else "sell",
            "reason": reason,
            "entry_state": state,
            "exit_price": current_price,
            "exit_notional": market_value if close_all else market_value * 0.50,
            "unrealized_pl_pct": round(pl_pct, 2),
            "order": result,
            "dry_run": dry_run,
        }
        self.journal.append(event)
        if close_all:
            try:
                from learning_engine import LearningEngine
                learner = LearningEngine()
                learner.record_strategy_outcome(
                    strategy_id=state.get("strategy", "unknown"),
                    regime=state.get("regime", "unknown"),
                    net_pl=market_value - _safe_float(state.get("entry_notional")),
                    symbol=symbol,
                    timeframe=state.get("timeframe") or "1h",
                    r_multiple=self._r_multiple(state, current_price),
                    false_signal=pl_pct < 0,
                )
            except Exception:
                pass
        self.logger.append(event_type, event)
        return event

    def _submit_or_dry(self, symbol, notional, side, dry_run):
        if dry_run:
            return {"id": "DRY-RUN", "symbol": symbol, "side": side, "notional": round(notional, 2), "status": "dry_run"}
        if self.client is None:
            self.client = AlpacaPaperClient()
        try:
            return self.client.submit_order(symbol, notional, side=side)
        except Exception as exc:
            return {"symbol": symbol, "side": side, "notional": round(notional, 2), "status": "error", "error": str(exc)}

    def _close_or_dry(self, symbol, dry_run):
        if dry_run:
            return {"id": "DRY-RUN-CLOSE", "symbol": symbol, "side": "close", "status": "dry_run"}
        if self.client is None:
            self.client = AlpacaPaperClient()
        try:
            return self.client.close_position(symbol)
        except Exception as exc:
            return {"symbol": symbol, "side": "close", "status": "error", "error": str(exc)}

    def _trailing_reason(self, symbol, state, current_price, high_water, partial_price):
        logic = state.get("trailing_stop_logic") or {}
        if not isinstance(logic, dict):
            logic = {}
        active = bool(state.get("trailing_active"))
        if partial_price and current_price >= partial_price:
            active = True
        if not active:
            return None
        atr_multiple = _safe_float(logic.get("atr_multiple"), 1.8)
        trail_price = _safe_float(state.get("trailing_stop_price"))
        try:
            candles = self.data.get_candles(symbol, "1h", limit=40)
            atr_values = atr(candles, 14) if candles else []
            atr_value = atr_values[-1] if atr_values else 0.0
            if atr_value > 0:
                trail_price = max(trail_price, high_water - (atr_value * atr_multiple))
        except Exception:
            pass
        if trail_price > 0:
            self.risk_book.update_fields(symbol, trailing_active=True, trailing_stop_price=round(trail_price, 6))
            if current_price <= trail_price:
                return f"Trailing stop hit: price {current_price:.4f} <= trail {trail_price:.4f}."
        return None

    def _invalidation_reason(self, symbol, state, current_price):
        strategy = str(state.get("strategy") or "")
        if strategy not in {
            "trend_pullback", "ema_crossover", "macd_momentum", "breakout_retest",
            "donchian_breakout", "atr_momentum_expansion", "supertrend_continuation",
        }:
            return None
        try:
            candles = self.data.get_candles(symbol, "1h", limit=80)
            if len(candles) < 50:
                return None
            from intraday_engine import ema
            closes = [_safe_float(c.get("close")) for c in candles]
            ema50 = ema(closes, 50)[-1]
            if current_price < ema50:
                return f"Signal invalidation: price {current_price:.4f} closed below 1H EMA50 {ema50:.4f}."
        except Exception:
            return None
        return None

    def _r_multiple(self, state, current_price):
        entry = _safe_float(state.get("entry_price"))
        stop = _safe_float(state.get("stop_loss_price"))
        if entry <= 0 or stop <= 0 or entry <= stop:
            return None
        return (current_price - entry) / (entry - stop)
