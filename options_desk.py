"""
Options desk (Stage 3) — runs one wheel cycle.

Gathers the account + positions from Alpaca, builds live chain/quote providers,
asks the wheel engine what to do, then executes (or logs, in dry-run). Saves a
state snapshot for the dashboard. Read-only until config.OPTIONS_LIVE is on.
"""

import os
import json
import datetime

import config
from options_data import get_live_put_chain, get_live_call_chain, get_contract_quote
from options_engine import decide_actions, engine_config
from options_executor import OptionsExecutor

STATE_FILE = os.path.join(config.DATA_DIR, "options_desk_state.json")


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_options_desk_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


class OptionsDesk:
    def run_cycle(self, dry_run=None):
        if dry_run is None:
            dry_run = not getattr(config, "OPTIONS_LIVE", False)

        cfg = engine_config()
        state = {
            "timestamp": _utcnow(), "mode": "options", "dry_run": dry_run,
            "underlyings": list(config.OPTIONS_UNDERLYINGS),
            "actions": [], "positions": [], "errors": [],
            "buying_power": None,
            "config": {"target_delta": cfg["target_delta"], "min_dte": cfg["min_dte"],
                       "max_dte": cfg["max_dte"], "profit_take": cfg["profit_take"],
                       "min_iv": cfg["min_iv"], "max_positions": cfg["max_positions"]},
        }

        key = os.environ.get("ALPACA_API_KEY", "")
        sec = os.environ.get("ALPACA_API_SECRET", "")
        if not (key and sec):
            state["errors"].append("Alpaca API keys not set on server")
            self._save(state)
            return state

        # Account + positions
        try:
            from alpaca.trading.client import TradingClient
            tc = TradingClient(api_key=key, secret_key=sec, paper=True)
            acct = tc.get_account()
            bp = float(getattr(acct, "options_buying_power", None)
                       or getattr(acct, "buying_power", 0) or 0)
            positions = []
            for p in tc.get_all_positions():
                positions.append({"symbol": str(getattr(p, "symbol", "")),
                                  "qty": float(getattr(p, "qty", 0) or 0),
                                  "avg_entry_price": float(getattr(p, "avg_entry_price", 0) or 0)})
            state["buying_power"] = round(bp, 2)
            state["positions"] = positions
        except Exception as e:
            state["errors"].append(f"account/positions fetch failed: {e}")
            self._save(state)
            return state

        # Open (unfilled) short-put orders occupy a slot + collateral, so the bot
        # doesn't pile up orders while limits sit unfilled.
        pending_puts = []
        try:
            from options_data import parse_occ_symbol
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            for o in tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100)):
                info = parse_occ_symbol(str(getattr(o, "symbol", "")))
                if not info or info["type"] != "put":
                    continue
                if "sell" not in str(getattr(o, "side", "")).lower():
                    continue
                qty = abs(float(getattr(o, "qty", 1) or 1))
                pending_puts.append({"root": info["root"], "collateral": info["strike"] * 100 * qty})
        except Exception as e:
            state["errors"].append(f"open-orders fetch failed: {e}")
        state["pending_puts"] = pending_puts

        # Live data providers (bounded DTE window with a little slack)
        max_dte = cfg["max_dte"] + 4

        def put_fn(u):
            return get_live_put_chain(u, key, sec, max_expirations=3, max_dte=max_dte)

        def call_fn(u):
            return get_live_call_chain(u, key, sec, max_expirations=3, max_dte=max_dte)

        def quote_fn(c):
            return get_contract_quote(c, key, sec)

        try:
            actions = decide_actions(config.OPTIONS_UNDERLYINGS, positions,
                                     put_fn, call_fn, quote_fn, bp, cfg,
                                     pending_puts=pending_puts)
        except Exception as e:
            state["errors"].append(f"decision engine failed: {e}")
            self._save(state)
            return state

        executor = OptionsExecutor(dry_run=dry_run)
        for a in actions:
            if a.get("action") == "hold":
                state["actions"].append({**a, "result": {"skipped": True}})
                continue
            try:
                res = executor.execute(a)
            except Exception as e:
                res = {"ok": False, "error": str(e)}
            state["actions"].append({**a, "result": res})

        self._save(state)
        return state

    def _save(self, state):
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            tmp = STATE_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, default=str, indent=2)
            os.replace(tmp, STATE_FILE)
        except Exception:
            pass
