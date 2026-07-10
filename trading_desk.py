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
from missed_opportunity import MissedOpportunityAnalyzer
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
        self.missed_analyzer = MissedOpportunityAnalyzer()

    def run_cycle(self, dry_run=None):
        dry_run = self.dry_run if dry_run is None else dry_run
        start = datetime.now(timezone.utc)
        account, positions, broker_note = self._load_account_and_positions(dry_run=dry_run)
        position_map = self._position_map(positions)

        # Set equity in conservative mode so all limits scale dynamically
        equity = float(account.get("equity", account.get("portfolio_value", 0)) or 0)
        if equity > 0:
            self.conservative.set_equity(equity)

        # CEO analysis runs first so regime-flip exit can use current regime
        ceo_state = self.ceo.analyze()

        # Market-trend filter ("don't fight the tape"): computed once per cycle.
        # When the broad market is in a downtrend, NEW long entries are blocked;
        # existing positions are still managed by the exit manager.
        try:
            from market_filter import market_trend_status
            market_status = market_trend_status(self.data)
        except Exception as _mf_exc:
            market_status = {"enabled": True, "allow_longs": True, "degraded": True,
                             "reason": f"Market filter error ({_mf_exc}) — longs allowed."}

        exit_result = self.exit_manager.check_exits(
            positions=positions, dry_run=dry_run,
            ceo_regime=ceo_state.market_regime,
        )

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
                # Record estimated exit fees
                if exit_notional > 0:
                    try:
                        from alpaca_client import is_equity_symbol
                        asset_class = "stock" if is_equity_symbol(exit_action.get("symbol", "")) else "crypto"
                    except Exception:
                        asset_class = entry_state.get("asset_class")
                    est_exit_fee = self.conservative.estimate_fee_for_notional(
                        exit_notional, asset_class=asset_class
                    )
                    self.conservative.record_fees(est_exit_fee)
                # Release open risk budget for closed positions
                if exit_action.get("event") == "position_closed":
                    self.conservative.release_open_risk(
                        exit_action.get("symbol", "")
                    )

        # Update unrealized P&L from current positions
        total_unrealized = sum(
            float(p.get("unrealized_pl") or 0) for p in positions or []
        )
        self.conservative.update_unrealized(total_unrealized)

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

            trade_req = decision.trade_request

            # ── Market-trend filter gate: block NEW longs in a downtrend ──
            if not market_status.get("allow_longs", True):
                self.logger.append("market_filter_block", {
                    "symbol": trade_req.get("symbol"),
                    "strategy": trade_req.get("strategy"),
                    "reason": market_status.get("reason"),
                    "timestamp": _utcnow(),
                })
                approvals.append({
                    "approved": False,
                    "symbol": trade_req.get("symbol"),
                    "reasons": [f"Market filter: {market_status.get('reason')}"],
                    "trade_request": trade_req,
                })
                continue

            # ── Conservative mode gate ──
            # Estimate proposed risk for open risk budget check
            proposed_risk = self.conservative.get_risk_per_trade()
            cm_ok, cm_reason = self.conservative.can_trade(
                symbol=trade_req.get("symbol"),
                strategy=trade_req.get("strategy"),
                risk_reward=float(trade_req.get("risk_reward") or 0),
                proposed_risk_dollars=proposed_risk,
                open_position_count=len(positions or []),
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
                    # Register open risk budget
                    risk_dollars = float(approval.get("risk_dollars") or 0)
                    if risk_dollars > 0:
                        self.conservative.register_open_risk(
                            decision.trade_request.get("symbol", ""),
                            risk_dollars,
                        )
                    # Record estimated fees for net P&L tracking
                    notional = float(approval.get("notional") or 0)
                    if notional > 0:
                        est_fee = self.conservative.estimate_fee_for_notional(
                            notional, asset_class=trade_req.get("asset_class")
                        )
                        self.conservative.record_fees(est_fee)
                if not order_result.get("error") and not dry_run:
                    try:
                        self.risk.record_order(symbol)
                        self.risk.record_submitted_order(symbol, "buy")
                    except Exception:
                        pass
            else:
                self.executor.execute(approval, dry_run=True)

        # Reconciliation: compare internal P&L with Alpaca account
        reconciliation = self._reconcile(account, positions)

        # Missed Opportunity Analyzer: record why trades were skipped
        try:
            self.missed_analyzer.record_cycle(
                managers=managers,
                ceo_state=ceo_state.to_dict(),
                approvals=approvals,
                conservative_status=self.conservative.get_status(),
            )
        except Exception as exc:
            import logging
            logging.getLogger("trading_desk").warning("Missed opportunity analyzer error: %s", exc)

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
        state["reconciliation"] = reconciliation
        state["market_filter"] = market_status
        try:
            state["missed_opportunities"] = self.missed_analyzer.get_status()
        except Exception:
            state["missed_opportunities"] = {}
        self.logger.save_cycle_state(state)
        return state

    def _reconcile(self, account, positions):
        """Compare internal P&L tracking with Alpaca's actual numbers.

        Returns a dict with discrepancy info for the dashboard.
        Logs a warning if the difference exceeds $5.
        """
        import logging
        _log = logging.getLogger("reconciliation")
        try:
            cm_status = self.conservative.get_status()
            internal_realized = cm_status.get("realized_pl", 0)
            internal_unrealized = cm_status.get("unrealized_pl", 0)
            internal_combined = internal_realized + internal_unrealized

            # Alpaca's actual unrealized P&L from positions
            alpaca_unrealized = sum(
                float(p.get("unrealized_pl") or 0) for p in positions or []
            )
            # Alpaca today's P&L if available from account
            alpaca_day_pl = float(account.get("equity", 0)) - float(account.get("last_equity", account.get("equity", 0)) or account.get("equity", 0))

            discrepancy_unrealized = round(internal_unrealized - alpaca_unrealized, 2)

            result = {
                "internal_realized": internal_realized,
                "internal_unrealized": internal_unrealized,
                "internal_combined": round(internal_combined, 2),
                "alpaca_unrealized": round(alpaca_unrealized, 2),
                "discrepancy_unrealized": discrepancy_unrealized,
                "positions_internal": len(cm_status.get("open_risk_slots", {})),
                "positions_alpaca": len(positions or []),
                "status": "OK",
            }

            # Flag discrepancies
            if abs(discrepancy_unrealized) > 5.0:
                result["status"] = "WARNING"
                _log.warning(
                    "Reconciliation discrepancy: internal unrealized $%.2f vs "
                    "Alpaca unrealized $%.2f (diff $%.2f)",
                    internal_unrealized, alpaca_unrealized, discrepancy_unrealized,
                )

            pos_diff = result["positions_internal"] - result["positions_alpaca"]
            if abs(pos_diff) > 0:
                result["position_mismatch"] = pos_diff
                if result["status"] == "OK":
                    result["status"] = "POSITION_MISMATCH"
                _log.warning(
                    "Position count mismatch: internal tracks %d, Alpaca has %d",
                    result["positions_internal"], result["positions_alpaca"],
                )

            return result
        except Exception as exc:
            return {"status": "ERROR", "error": str(exc)}

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
        approval_by_symbol = {
            _compact_symbol(a.get("symbol") or (a.get("trade_request") or {}).get("symbol")): a
            for a in approvals or []
        }
        order_by_symbol = {
            _compact_symbol(o.get("symbol")): o
            for o in orders or []
        }
        for manager in managers:
            selected = manager.get("selected_signal") or {}
            strategy_signal = dict(selected)
            timeframe = strategy_signal.get("timeframe") or config.DESK_ENTRY_TIMEFRAME
            if strategy_signal:
                strategy_signal.setdefault("strategy", manager.get("active_strategy"))
                strategy_signal.setdefault("confidence", manager.get("confidence", 0))
                strategy_signal.setdefault("timeframe", timeframe)
            symbol = manager.get("symbol")
            approval = approval_by_symbol.get(_compact_symbol(symbol), {})
            order = order_by_symbol.get(_compact_symbol(symbol), {})
            order_status = str(order.get("status", "") or "").lower()
            order_failed = order_status in {"rejected", "canceled", "cancelled", "expired", "error"} or bool(order.get("error"))
            order_submitted = bool(order) and not order_failed
            risk_approved = bool(approval.get("approved"))
            manager_selected = manager.get("action") == "enter"
            symbols[symbol] = {
                "symbol": symbol,
                "accepted": order_submitted,
                "manager_selected": manager_selected,
                "risk_approved": risk_approved,
                "order_submitted": order_submitted,
                "action": "buy" if order_submitted else "hold",
                "confidence": manager.get("confidence", 0),
                "reason": manager.get("reason"),
                "approval_reason": "; ".join(approval.get("reasons", [])) if approval else "",
                "order_status": order_status or None,
                "strategy_name": manager.get("active_strategy"),
                "timeframe": timeframe,
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

        def _order_ok(order):
            status = str(order.get("status", "") or "").lower()
            failed = status in {"rejected", "canceled", "cancelled", "expired", "error"}
            return bool(order) and not failed and not order.get("error")

        manager_enter_count = sum(1 for row in managers if row.get("action") == "enter")
        risk_approved_count = sum(1 for row in approvals if row.get("approved"))
        order_submitted_count = len([o for o in orders if _order_ok(o)])
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
                "enter_decisions": manager_enter_count,
                "manager_enter_decisions": manager_enter_count,
                "risk_approved_entries": risk_approved_count,
                "blocked_after_manager": max(0, manager_enter_count - risk_approved_count),
                "wait_decisions": sum(1 for row in managers if row.get("action") in ("wait", "cooldown")),
                "manage_decisions": sum(1 for row in managers if row.get("action") == "manage"),
                "orders_submitted": order_submitted_count,
                "orders_rejected": len([a for a in approvals if not a.get("approved")]),
                "exits_triggered": len((exit_result or {}).get("actions", [])),
            },
        }


def run_trading_desk_cycle(dry_run=False):
    return TradingDeskEngine(dry_run=dry_run).run_cycle(dry_run=dry_run)


def load_state():
    return load_trading_desk_state()
