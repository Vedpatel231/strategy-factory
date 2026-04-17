"""
Strategy Factory — Local Paper Trading Broker

Self-contained paper trading simulator. Starts with $1,000 cash, marks
positions using a synthetic math model, and persists state to
`data/paper_account.json`.

No external broker account required. Replaces the Alpaca integration.

Public API (mirrors the Alpaca client so paper_trader.py doesn't care):
    get_account(), get_positions(), get_position(symbol),
    get_latest_price(symbol), submit_order(symbol, notional, side),
    close_position(symbol), close_all_positions(),
    get_orders(limit, status), reset_account(starting_balance)
"""

import os
import json
import uuid
import logging
import datetime

logger = logging.getLogger("paper_broker")

_DATA_DIR = os.environ.get("STRATEGY_FACTORY_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
STATE_FILE = os.path.join(_DATA_DIR, "paper_account.json")
DEFAULT_STARTING_BALANCE = 1000.0
DEFAULT_SYNTHETIC_PRICE = 100.0
MONTH_SECONDS = 30 * 24 * 60 * 60
MARK_TO_MODEL_TIME_ACCELERATION = float(os.environ.get("PAPER_BROKER_TIME_ACCELERATION", "1440"))

# All symbols our bots trade — normalized to a simple internal symbol format.
SUPPORTED_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT",
    "DOTUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT", "DOGEUSDT", "UNIUSDT",
    "ATOMUSDT", "FTMUSDT", "NEARUSDT", "ALGOUSDT", "APEUSDT", "CRVUSDT",
    "LTCUSDT", "BCHUSDT",
}


def normalize_symbol(pair):
    """'BTC/USDT' or 'BTCUSD' → 'BTCUSDT'. Returns None if unsupported."""
    if not pair:
        return None
    p = pair.upper().replace("/", "")
    if p.endswith("USD") and not p.endswith("USDT"):
        p = p + "T"  # BTCUSD → BTCUSDT
    return p if p in SUPPORTED_SYMBOLS else None


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def parse_iso(ts):
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except Exception as e:
        logger.warning(f"Could not parse timestamp '{ts}': {e}")
        return utc_now()


# ── BROKER ──────────────────────────────────────────────────────────────
class PaperBroker:
    """Local paper trading simulator with synthetic mark-to-model pricing."""

    def __init__(self, starting_balance=DEFAULT_STARTING_BALANCE):
        self.starting_balance = starting_balance
        self.state = self._load_or_init()

    def _load_or_init(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load {STATE_FILE}: {e}. Re-initializing.")
        return self._fresh_state()

    def _fresh_state(self):
        now = utc_now().isoformat()
        return {
            "account_number": "PAPER-" + str(uuid.uuid4())[:8].upper(),
            "starting_balance": self.starting_balance,
            "cash": self.starting_balance,
            "realized_pl": 0.0,
            "positions": {},       # symbol -> synthetic position state
            "orders": [],          # newest last
            "created_at": now,
            "reset_count": 0,
        }

    def _save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2, default=str)

    def _default_price(self, symbol):
        seed = sum(ord(c) for c in symbol) % 75
        return round(DEFAULT_SYNTHETIC_PRICE + seed, 4)

    def _normalize_model_return(self, value):
        try:
            monthly_pct = float(value)
        except (TypeError, ValueError):
            monthly_pct = 0.0
        return max(-95.0, min(300.0, monthly_pct))

    def _mark_position(self, symbol, pos, as_of=None):
        now = as_of or utc_now()
        current_price = float(pos.get("current_price") or pos.get("avg_entry_price") or self._default_price(symbol))
        last_marked = parse_iso(pos.get("last_marked_at") or pos.get("opened_at") or now.isoformat())
        elapsed = max(0.0, (now - last_marked).total_seconds()) * max(MARK_TO_MODEL_TIME_ACCELERATION, 1.0)
        monthly_pct = self._normalize_model_return(pos.get("model_monthly_return_pct", 0.0))
        if elapsed > 0:
            growth_factor = max(0.01, 1 + (monthly_pct / 100.0))
            current_price *= growth_factor ** (elapsed / MONTH_SECONDS)
        pos["current_price"] = current_price
        pos["last_marked_at"] = now.isoformat()
        pos["model_monthly_return_pct"] = monthly_pct
        return pos

    def _mark_all_positions(self):
        now = utc_now()
        changed = False
        for sym, pos in self.state["positions"].items():
            before = pos.get("last_marked_at")
            self._mark_position(sym, pos, as_of=now)
            changed = changed or before != pos.get("last_marked_at")
        if changed:
            self._save()

    # ── ACCOUNT ──────────────────────────────────────────────────────────
    def get_account(self):
        """Return account snapshot matching the Alpaca interface shape."""
        positions = self.get_positions()
        market_value = sum(p["market_value"] for p in positions)
        equity = round(self.state["cash"] + market_value, 2)
        return {
            "account_number": self.state["account_number"],
            "status": "ACTIVE",
            "equity": equity,
            "cash": round(self.state["cash"], 2),
            "buying_power": round(self.state["cash"], 2),  # no margin for crypto
            "portfolio_value": equity,
            "pattern_day_trader": False,
            "currency": "USD",
            "paper": True,
            "starting_balance": self.state["starting_balance"],
            "realized_pl": round(self.state.get("realized_pl", 0), 2),
            "total_pl": round(equity - self.state["starting_balance"], 2),
            "total_pl_pct": round((equity - self.state["starting_balance"]) / self.state["starting_balance"] * 100, 2),
        }

    def reset_account(self, starting_balance=None):
        """Wipe positions and orders, restore to starting balance."""
        sb = starting_balance if starting_balance is not None else self.state.get("starting_balance", DEFAULT_STARTING_BALANCE)
        count = self.state.get("reset_count", 0) + 1
        self.state = self._fresh_state()
        self.state["starting_balance"] = sb
        self.state["cash"] = sb
        self.state["reset_count"] = count
        self._save()
        return self.get_account()

    # ── POSITIONS ────────────────────────────────────────────────────────
    def get_positions(self):
        self._mark_all_positions()
        out = []
        for sym, pos in self.state["positions"].items():
            current_price = float(pos.get("current_price") or pos.get("avg_entry_price") or self._default_price(sym))
            qty = pos["qty"]
            cost_basis = pos["cost_basis"]
            market_value = round(qty * current_price, 4)
            unrealized_pl = round(market_value - cost_basis, 4)
            unrealized_plpc = (unrealized_pl / cost_basis * 100) if cost_basis > 0 else 0
            out.append({
                "symbol": sym,
                "qty": qty,
                "avg_entry_price": round(pos["avg_entry_price"], 4),
                "cost_basis": round(cost_basis, 2),
                "current_price": round(current_price, 4),
                "market_value": market_value,
                "unrealized_pl": unrealized_pl,
                "unrealized_plpc": round(unrealized_plpc, 2),
                "side": "long",
                "model_monthly_return_pct": round(pos.get("model_monthly_return_pct", 0.0), 2),
            })
        return out

    def get_position(self, symbol):
        sym = normalize_symbol(symbol)
        if sym not in self.state["positions"]:
            return None
        # Re-use the full enriching path
        for p in self.get_positions():
            if p["symbol"] == sym:
                return p
        return None

    def get_latest_price(self, symbol):
        sym = normalize_symbol(symbol)
        if not sym:
            return None
        pos = self.state["positions"].get(sym)
        if pos:
            self._mark_position(sym, pos)
            self._save()
            return round(pos.get("current_price", self._default_price(sym)), 4)
        return self._default_price(sym)

    # ── ORDERS ───────────────────────────────────────────────────────────
    def submit_order(self, symbol, notional_usd, side="buy", model_monthly_return_pct=None):
        """Simulate a market order using synthetic prices and a monthly return model."""
        sym = normalize_symbol(symbol)
        if not sym:
            return {"error": f"Unsupported symbol: {symbol}", "symbol": symbol, "notional": notional_usd}
        if notional_usd <= 0:
            return {"error": "Notional must be positive", "symbol": sym}

        side = side.lower()
        order_id = "o-" + uuid.uuid4().hex[:10]
        now = utc_now().isoformat()
        monthly_model = self._normalize_model_return(model_monthly_return_pct)

        if side == "buy":
            price = self.get_latest_price(sym)
            if notional_usd > self.state["cash"] + 0.01:
                return {"error": f"Insufficient cash: have ${self.state['cash']:.2f}, need ${notional_usd:.2f}", "symbol": sym}
            qty = notional_usd / price
            self.state["cash"] -= notional_usd
            pos = self.state["positions"].get(sym)
            if pos:
                self._mark_position(sym, pos)
                existing_value = pos["qty"] * pos.get("current_price", price)
                new_qty = pos["qty"] + qty
                new_cost = pos["cost_basis"] + notional_usd
                pos["qty"] = new_qty
                pos["cost_basis"] = new_cost
                pos["avg_entry_price"] = new_cost / new_qty if new_qty else 0
                pos["current_price"] = price
                if model_monthly_return_pct is not None:
                    total_weight = existing_value + notional_usd
                    if total_weight > 0:
                        pos["model_monthly_return_pct"] = (
                            (pos.get("model_monthly_return_pct", 0.0) * existing_value)
                            + (monthly_model * notional_usd)
                        ) / total_weight
                pos["last_marked_at"] = now
            else:
                self.state["positions"][sym] = {
                    "qty": qty,
                    "avg_entry_price": price,
                    "cost_basis": notional_usd,
                    "opened_at": now,
                    "last_marked_at": now,
                    "current_price": price,
                    "model_monthly_return_pct": monthly_model,
                }
            order = {
                "id": order_id, "symbol": sym, "side": "buy",
                "notional": round(notional_usd, 2), "qty": round(qty, 8),
                "filled_avg_price": round(price, 4),
                "status": "filled",
                "submitted_at": now, "filled_at": now,
            }
        elif side == "sell":
            pos = self.state["positions"].get(sym)
            if not pos:
                return {"error": f"No position in {sym} to sell", "symbol": sym}
            self._mark_position(sym, pos)
            price = pos.get("current_price", self._default_price(sym))
            sell_qty = min(pos["qty"], notional_usd / price)
            sell_notional = sell_qty * price
            # Realize pro-rata cost basis
            pro_rata_cost = pos["cost_basis"] * (sell_qty / pos["qty"]) if pos["qty"] else 0
            realized = sell_notional - pro_rata_cost
            self.state["realized_pl"] = self.state.get("realized_pl", 0) + realized
            self.state["cash"] += sell_notional
            pos["qty"] -= sell_qty
            pos["cost_basis"] -= pro_rata_cost
            if pos["qty"] <= 1e-9:
                del self.state["positions"][sym]
            else:
                pos["last_marked_at"] = now
            order = {
                "id": order_id, "symbol": sym, "side": "sell",
                "notional": round(sell_notional, 2), "qty": round(sell_qty, 8),
                "filled_avg_price": round(price, 4),
                "status": "filled",
                "realized_pl": round(realized, 4),
                "submitted_at": now, "filled_at": now,
            }
        else:
            return {"error": f"Invalid side: {side}", "symbol": sym}

        self.state["orders"].append(order)
        # Keep only last 200 orders
        self.state["orders"] = self.state["orders"][-200:]
        self._save()
        return order

    def close_position(self, symbol):
        sym = normalize_symbol(symbol)
        if not sym:
            return {"error": f"Unsupported symbol: {symbol}"}
        pos = self.state["positions"].get(sym)
        if not pos:
            return {"error": f"No position in {sym}"}
        self._mark_position(sym, pos)
        price = pos.get("current_price", self._default_price(sym))
        notional = pos["qty"] * price
        return self.submit_order(sym, notional, side="sell")

    def close_all_positions(self):
        results = []
        for sym in list(self.state["positions"].keys()):
            results.append(self.close_position(sym))
        return results

    def get_orders(self, limit=50, status="all"):
        orders = list(reversed(self.state.get("orders", [])))  # newest first
        if status != "all":
            orders = [o for o in orders if o.get("status") == status]
        return orders[:limit]
