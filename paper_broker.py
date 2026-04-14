"""
Strategy Factory — Local Paper Trading Broker

Self-contained paper trading simulator. Starts with $1,000 cash, fetches real
crypto prices from Binance for valuation, and persists state to
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
import urllib.request
import urllib.error

logger = logging.getLogger("paper_broker")

_DATA_DIR = os.environ.get("STRATEGY_FACTORY_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
STATE_FILE = os.path.join(_DATA_DIR, "paper_account.json")
DEFAULT_STARTING_BALANCE = 1000.0
BINANCE_BASE = "https://api.binance.com"

# All symbols our bots trade — normalized to Binance spot format
# (BTC/USDT → BTCUSDT)
SUPPORTED_SYMBOLS = {
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT",
    "DOTUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT", "DOGEUSDT", "UNIUSDT",
    "ATOMUSDT", "FTMUSDT", "NEARUSDT", "ALGOUSDT", "APEUSDT", "CRVUSDT",
    "LTCUSDT", "BCHUSDT",
}


def normalize_symbol(pair):
    """'BTC/USDT' or 'BTCUSD' → 'BTCUSDT' (Binance format). Returns None if unsupported."""
    if not pair:
        return None
    p = pair.upper().replace("/", "")
    if p.endswith("USD") and not p.endswith("USDT"):
        p = p + "T"  # BTCUSD → BTCUSDT
    return p if p in SUPPORTED_SYMBOLS else None


# ── PRICE FETCH ─────────────────────────────────────────────────────────
_price_cache = {}
_price_cache_ts = {}
PRICE_CACHE_SECS = 5  # short cache to avoid hammering Binance during UI refreshes


def get_binance_price(symbol, timeout=5):
    """Fetch latest spot price for a Binance symbol (e.g. 'BTCUSDT')."""
    now = datetime.datetime.now().timestamp()
    cached_ts = _price_cache_ts.get(symbol, 0)
    if symbol in _price_cache and (now - cached_ts) < PRICE_CACHE_SECS:
        return _price_cache[symbol]
    url = f"{BINANCE_BASE}/api/v3/ticker/price?symbol={symbol}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StrategyFactory/3.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            price = float(data["price"])
            _price_cache[symbol] = price
            _price_cache_ts[symbol] = now
            return price
    except urllib.error.HTTPError as e:
        if e.code == 400:
            logger.warning(f"Binance: symbol {symbol} not found")
            return None
        raise
    except Exception as e:
        logger.warning(f"Price fetch failed for {symbol}: {e}")
        return None


# ── BROKER ──────────────────────────────────────────────────────────────
class PaperBroker:
    """Local paper trading simulator with persistence."""

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
        return {
            "account_number": "PAPER-" + str(uuid.uuid4())[:8].upper(),
            "starting_balance": self.starting_balance,
            "cash": self.starting_balance,
            "realized_pl": 0.0,
            "positions": {},       # symbol -> {qty, cost_basis, avg_entry_price, opened_at}
            "orders": [],          # newest last
            "created_at": datetime.datetime.utcnow().isoformat(),
            "reset_count": 0,
        }

    def _save(self):
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(self.state, f, indent=2, default=str)

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
        out = []
        for sym, pos in self.state["positions"].items():
            current_price = get_binance_price(sym)
            if current_price is None:
                current_price = pos.get("avg_entry_price", 0)
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
        return get_binance_price(sym)

    # ── ORDERS ───────────────────────────────────────────────────────────
    def submit_order(self, symbol, notional_usd, side="buy"):
        """Simulate a market order at current Binance price."""
        sym = normalize_symbol(symbol)
        if not sym:
            return {"error": f"Unsupported symbol: {symbol}", "symbol": symbol, "notional": notional_usd}
        if notional_usd <= 0:
            return {"error": "Notional must be positive", "symbol": sym}

        price = get_binance_price(sym)
        if price is None:
            return {"error": f"Could not fetch price for {sym}", "symbol": sym}

        side = side.lower()
        order_id = "o-" + uuid.uuid4().hex[:10]
        now = datetime.datetime.utcnow().isoformat()

        if side == "buy":
            if notional_usd > self.state["cash"] + 0.01:
                return {"error": f"Insufficient cash: have ${self.state['cash']:.2f}, need ${notional_usd:.2f}", "symbol": sym}
            qty = notional_usd / price
            self.state["cash"] -= notional_usd
            pos = self.state["positions"].get(sym)
            if pos:
                new_qty = pos["qty"] + qty
                new_cost = pos["cost_basis"] + notional_usd
                pos["qty"] = new_qty
                pos["cost_basis"] = new_cost
                pos["avg_entry_price"] = new_cost / new_qty if new_qty else 0
            else:
                self.state["positions"][sym] = {
                    "qty": qty,
                    "avg_entry_price": price,
                    "cost_basis": notional_usd,
                    "opened_at": now,
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
        price = get_binance_price(sym)
        if price is None:
            return {"error": f"Could not fetch price for {sym}"}
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
