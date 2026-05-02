"""Order execution for approved Strategy Factory desk requests."""

from datetime import datetime, timezone

from alpaca_client import AlpacaPaperClient, is_configured
from decision_logger import DecisionLogger
from trade_journal import PositionRiskBook, TradeJournal


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


class TradeExecutor:
    def __init__(self, client=None, journal=None, risk_book=None, logger=None):
        self.client = client
        self.journal = journal or TradeJournal()
        self.risk_book = risk_book or PositionRiskBook()
        self.logger = logger or DecisionLogger()

    def execute(self, approval, dry_run=False):
        request = approval.get("trade_request") or {}
        symbol = approval.get("symbol") or request.get("symbol")
        side = approval.get("side") or request.get("side", "buy")
        notional = float(approval.get("notional") or 0)
        result = {
            "timestamp": _utcnow(),
            "symbol": symbol,
            "side": side,
            "notional": round(notional, 2),
            "dry_run": dry_run,
            "approval": approval,
        }

        if not approval.get("approved"):
            result.update({"status": "rejected", "error": "; ".join(approval.get("reasons", []))})
            self.logger.append("order_rejected", result)
            self.journal.append({
                "event": "entry_rejected",
                "symbol": symbol,
                "strategy": request.get("strategy"),
                "confidence": request.get("confidence"),
                "reason": result["error"],
                "signal": request,
            })
            return result

        if dry_run:
            order = {
                "id": "DRY-RUN",
                "symbol": symbol,
                "side": side,
                "notional": round(notional, 2),
                "status": "dry_run",
                "filled_avg_price": request.get("entry_price"),
            }
        else:
            if self.client is None:
                if not is_configured():
                    result.update({"status": "error", "error": "Alpaca API keys are not configured."})
                    self.logger.append("order_error", result)
                    return result
                self.client = AlpacaPaperClient()
            try:
                order = self.client.submit_order(symbol, notional, side=side)
            except Exception as exc:
                result.update({"status": "error", "error": str(exc)})
                self.logger.append("order_error", result)
                return result

        result.update({"status": order.get("status", "submitted"), "order": order})
        self.logger.append("order_submitted", result)
        self.journal.append({
            "event": "order_submitted",
            "symbol": symbol,
            "side": side,
            "notional": round(notional, 2),
            "status": order.get("status"),
            "bot_names": [request.get("bot_name")],
            "strategy": request.get("strategy"),
            "regime": request.get("ceo_regime"),
            "confidence": request.get("confidence"),
            "entry_reason": request.get("entry_reason"),
            "order": order,
            "signal": request,
        })

        if side == "buy" and not order.get("error") and not dry_run:
            entry_price = order.get("filled_avg_price") or request.get("entry_price")
            entry_notional = notional
            if self.client is not None and not dry_run:
                try:
                    position = self.client.get_position(symbol)
                    if position:
                        entry_price = position.get("avg_entry_price") or entry_price
                        entry_notional = position.get("cost_basis") or entry_notional
                except Exception:
                    pass
            self.risk_book.register_entry(
                symbol=symbol,
                strategy=request.get("strategy"),
                regime=request.get("ceo_regime"),
                confidence=request.get("confidence"),
                entry_price=entry_price,
                notional=entry_notional,
                stop_loss_pct=self._pct_distance(entry_price, request.get("stop_loss"), "down"),
                take_profit_pct=self._pct_distance(entry_price, request.get("take_profit"), "up"),
                trailing_stop_pct=0.0,
                max_hold_hours=96,
                reason=request.get("entry_reason"),
                bot_names=[request.get("bot_name")],
                stop_loss_price=request.get("stop_loss"),
                take_profit_price=request.get("take_profit"),
                partial_profit_price=request.get("partial_profit"),
                trailing_stop_logic=request.get("trailing_stop"),
                risk_reward=request.get("risk_reward"),
                timeframe=request.get("timeframe"),
            )
        return result

    def _pct_distance(self, entry, level, direction):
        try:
            entry = float(entry or 0)
            level = float(level or 0)
            if entry <= 0 or level <= 0:
                return 0.0
            if direction == "down":
                return max(0.0, (entry - level) / entry * 100.0)
            return max(0.0, (level - entry) / entry * 100.0)
        except Exception:
            return 0.0
