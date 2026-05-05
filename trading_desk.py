"""Professional Strategy Factory trading desk orchestration."""

import os
from datetime import datetime, timezone

import config
from alpaca_client import AlpacaPaperClient, is_configured, normalize_crypto_symbol
from asset_manager import AssetManager
from bot_registry import BotRegistry
from conservative_mode import ConservativeMode
from decision_logger import DecisionLogger, load_trading_desk_state
from exit_manager import ExitManager
from intraday_engine import MarketDataProvider
from learning_engine import LearningEngine
from market_ceo import MarketCEO
from risk_manager import RiskManager
from trade_executor import TradeExecutor


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _compact_symbol(symbol):
    return str(symbol or "").upper().replace("/", "")


class TradingDeskEngine:
    def __init__(self, data_provider=None, client=None, dry_run=False):
        self.data = data_provider or MarketDataProvider()
        self.client = client
        self.dry_run = dry_run
        self.logger = DecisionLogger()
        self.registry = BotRegistry()
        self.learner = LearningEngine()
        # Reset learning state if it was trained on corrupted P&L data.
        # The partial profit accounting bug caused massively overstated losses,
        # which poisoned the learning engine's strategy scores.  This flag file
        # is checked once and removed after reset.
        _reset_flag = os.path.join(config.DATA_DIR, "learning_needs_reset")
        if not os.path.exists(os.path.join(config.DATA_DIR, "learning_reset_done")):
            self.learner.reset_all()
            try:
                with open(os.path.join(config.DATA_DIR, "learning_reset_done"), "w") as _f:
                    _f.write(_utcnow())
            except Exception:
                pass
        self.ceo = MarketCEO(data_provider=self.data)
        self.risk = RiskManager()
        self.conservative = ConservativeMode()
        self.executor = TradeExecutor(client=self.client, logger=self.logger)
        self.exit_manager = ExitManager(client=self.client, data_provider=self.data, logger=self.logger)

    def run_cycle(self, dry_run=None):
        dry_run = self.dry_run if dry_run is None else dry_run
        start = datetime.now(timezone.utc)
        account, positions, broker_note = self._load_account_and_positions(dry_run=dry_run)
        position_map = self._position_map(positions)

        exit_result = self.exit_manager.check_exits(positions=positions, dry_run=dry_run)

        # Record exit results in conservative mode for daily P&L tracking
        for exit_action in (exit_result or {}).get("actions", []):
            if exit_action.get("event") in ("position_closed", "partial_profit"):
                entry_state = exit_action.get("entry_state") or {}
                exit_notional = float(exit_action.get("exit_notional") or 0)
                entry_notional = float(entry_state.get("entry_notional") or 0)
                net_pl = exit_notional - entry_notional if entry_notional else 0
                self.conservative.record_trade_result(
                    symbol=exit_action.get("symbol", ""),
                    strategy=entry_state.get("strategy", "unknown"),
                    net_pl=net_pl,
                    reason=exit_action.get("reason", ""),
                )

        # Update unrealized P&L from current positions
        total_unrealized = sum(
            float(p.get("unrealized_pl") or 0) for p in positions or []
        )
        self.conservative.update_unrealized(total_unrealized)

        ceo_state = self.ceo.analyze()

        managers = []
        approvals = []
        orders = []
        grouped = self.registry.by_asset()
        for symbol, bots in grouped.items():
            asset_class = bots[0].asset_class if bots else "crypto"
            manager = AssetManager(
                symbol=symbol,
                asset_class=asset_class,
                bots=bots,
                data_provider=self.data,
                learner=self.learner,
                logger=self.logger,
            )
            decision = manager.evaluate(ceo_state, open_position=position_map.get(_compact_symbol(symbol)))
            managers.append(decision.to_dict())

            if not decision.trade_request:
                continue

            # ── Conservative mode gate ──
            trade_req = decision.trade_request
            cm_ok, cm_reason = self.conservative.can_trade(
                symbol=trade_req.get("symbol"),
                strategy=trade_req.get("strategy"),
                risk_reward=float(trade_req.get("risk_reward") or 0),
            )
            if not cm_ok:
                # Log the rejection but don't send to risk manager
                self.logger.append("conservative_block", {
                    "symbol": trade_req.get("symbol"),
                    "strategy": trade_req.get("strategy"),
                    "reason": cm_reason,
                    "timestamp": _utcnow(),
                })
                approvals.append({
                    "approved": False,
                    "symbol": trade_req.get("symbol"),
                    "reasons": [cm_reason],
                    "trade_request": trade_req,
                })
                continue

            approval = self.risk.approve_trade_request(
                decision.trade_request,
                account=account,
                open_positions=positions,
                ceo_state=ceo_state,
            )
            approvals.append(approval)
            if approval.get("approved"):
                order_result = self.executor.execute(approval, dry_run=dry_run)
                orders.append(order_result)
                if not order_result.get("error"):
                    pseudo_position = {
                        "symbol": decision.trade_request.get("symbol"),
                        "market_value": approval.get("notional", 0),
                        "current_price": decision.trade_request.get("entry_price"),
                        "avg_entry_price": decision.trade_request.get("entry_price"),
                    }
                    positions.append(pseudo_position)
                    position_map[_compact_symbol(pseudo_position["symbol"])] = pseudo_position
                if not order_result.get("error") and not dry_run:
                    try:
                        self.risk.record_order(symbol)
                        self.risk.record_submitted_order(symbol, "buy")
                    except Exception:
                        pass
            else:
                self.executor.execute(approval, dry_run=True)

        duration = (datetime.now(timezone.utc) - start).total_seconds()
        state = self._build_state(
            ceo_state=ceo_state,
            managers=managers,
            approvals=approvals,
            orders=orders,
            exit_result=exit_result,
            account=account,
            positions=positions,
            duration=duration,
            dry_run=dry_run,
            broker_note=broker_note,
        )
        self.logger.save_cycle_state(state)
        return state

    def _load_account_and_positions(self, dry_run=False):
        if self.client is None and is_configured():
            try:
                self.client = AlpacaPaperClient()
                self.executor.client = self.client
                self.exit_manager.client = self.client
            except Exception as exc:
                return self._mock_account(), [], f"Alpaca client unavailable: {exc}"
        if self.client is None:
            return self._mock_account(), [], "Alpaca API keys not configured; cycle ran in diagnostic mode."
        try:
            account = self.client.get_account()
            positions = self.client.get_positions(live_prices=True)
            return account, positions, ""
        except Exception as exc:
            return self._mock_account(), [], f"Alpaca account/position fetch failed: {exc}"

    def _mock_account(self):
        return {
            "equity": 1000.0,
            "cash": 1000.0,
            "buying_power": 1000.0,
            "paper": True,
            "broker": "diagnostic",
        }

    def _position_map(self, positions):
        out = {}
        for pos in positions or []:
            sym = normalize_crypto_symbol(pos.get("symbol"))
            out[_compact_symbol(sym)] = pos
            out[_compact_symbol(pos.get("raw_symbol"))] = pos
        return out

    def _build_state(self, ceo_state, managers, approvals, orders, exit_result, account,
                     positions, duration, dry_run, broker_note):
        symbols = {}
        for manager in managers:
            selected = manager.get("selected_signal") or {}
            strategy_signal = dict(selected)
            if strategy_signal:
                strategy_signal.setdefault("strategy", manager.get("active_strategy"))
                strategy_signal.setdefault("confidence", manager.get("confidence", 0))
                strategy_signal.setdefault("timeframe", "1h")
            symbol = manager.get("symbol")
            symbols[symbol] = {
                "symbol": symbol,
                "accepted": manager.get("action") == "enter",
                "action": "buy" if manager.get("action") == "enter" else "hold",
                "confidence": manager.get("confidence", 0),
                "reason": manager.get("reason"),
                "strategy_name": manager.get("active_strategy"),
                "timeframe": "1h",
                "strategy_signals": [strategy_signal] if strategy_signal else [],
                "trade_regime": {
                    "label": ceo_state.market_regime,
                    "confidence": ceo_state.confidence,
                    "trend_bias": ceo_state.market_direction,
                    "atr_pct": (selected.get("metadata") or {}).get("atr_pct", 0),
                    "reason": "; ".join(ceo_state.reasons),
                },
                "setup_regime": {
                    "label": ceo_state.market_regime,
                    "confidence": ceo_state.confidence,
                    "trend_bias": ceo_state.market_direction,
                    "reason": manager.get("reason"),
                },
                "features": (selected.get("features") or {}),
                "manager": manager,
                "evaluated_at": manager.get("timestamp"),
            }

        return {
            "source": "professional_trading_desk",
            "updated_at": _utcnow(),
            "dry_run": dry_run,
            "conservative_mode": self.conservative.get_status(),
            "duration_sec": round(duration, 2),
            "broker_note": broker_note,
            "registry": self.registry.summary(),
            "ceo": ceo_state.to_dict(),
            "managers": managers,
            "symbols": symbols,
            "risk_approvals": approvals,
            "orders": orders,
            "exit_manager": exit_result,
            "account": {
                "equity": account.get("equity"),
                "cash": account.get("cash"),
                "buying_power": account.get("buying_power"),
                "broker": account.get("broker", "alpaca"),
            },
            "positions": positions,
            "summary": {
                "managers": len(managers),
                "bots": self.registry.summary().get("bots", 0),
                "enter_decisions": sum(1 for row in managers if row.get("action") == "enter"),
                "wait_decisions": sum(1 for row in managers if row.get("action") in ("wait", "cooldown")),
                "manage_decisions": sum(1 for row in managers if row.get("action") == "manage"),
                "orders_submitted": len([o for o in orders if not o.get("error")]),
                "orders_rejected": len([a for a in approvals if not a.get("approved")]),
                "exits_triggered": len((exit_result or {}).get("actions", [])),
            },
        }


def run_trading_desk_cycle(dry_run=False):
    return TradingDeskEngine(dry_run=dry_run).run_cycle(dry_run=dry_run)


def load_state():
    return load_trading_desk_state()
