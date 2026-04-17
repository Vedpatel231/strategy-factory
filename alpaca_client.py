"""
Strategy Factory — Alpaca Paper Trading Client

Wraps the alpaca-py SDK for paper trading. Provides the same interface shape
as PaperBroker so the dashboard server can call either interchangeably.

Requires env vars:
    ALPACA_API_KEY      — your APCA-API-KEY-ID
    ALPACA_API_SECRET   — your APCA-API-SECRET-KEY

Alpaca paper trading base URL is used automatically.
"""

import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("alpaca_client")

# Paper trading base URL
ALPACA_PAPER_BASE = "https://paper-api.alpaca.markets"

_ALPACA_KEY = os.environ.get("ALPACA_API_KEY", "")
_ALPACA_SECRET = os.environ.get("ALPACA_API_SECRET", "")


def is_configured():
    """Return True if Alpaca API keys are present in environment."""
    return bool(_ALPACA_KEY and _ALPACA_SECRET)


def _get_trading_client():
    """Lazy import + create TradingClient from alpaca-py."""
    from alpaca.trading.client import TradingClient
    return TradingClient(
        api_key=_ALPACA_KEY,
        secret_key=_ALPACA_SECRET,
        paper=True,
    )


def _get_data_client():
    """Lazy import + create CryptoHistoricalDataClient from alpaca-py."""
    from alpaca.data.historical.crypto import CryptoHistoricalDataClient
    return CryptoHistoricalDataClient(
        api_key=_ALPACA_KEY,
        secret_key=_ALPACA_SECRET,
    )


class AlpacaPaperClient:
    """
    Alpaca paper trading client.

    Mirrors PaperBroker's public API so the dashboard can use either.
    """

    def __init__(self):
        if not is_configured():
            raise RuntimeError(
                "Alpaca API keys not configured. "
                "Set ALPACA_API_KEY and ALPACA_API_SECRET environment variables."
            )
        self._trading = _get_trading_client()
        self._connected = False
        self._account_number = None

    # ── CONNECTION ──────────────────────────────────────────────────────
    def connect(self):
        """Test connection by fetching account. Returns account dict or raises."""
        acct = self._trading.get_account()
        self._connected = True
        self._account_number = acct.account_number
        return self._format_account(acct)

    def _format_account(self, acct):
        """Convert Alpaca Account object to a plain dict matching our shape."""
        equity = float(acct.equity)
        cash = float(acct.cash)
        buying_power = float(acct.buying_power)
        last_equity = float(acct.last_equity) if acct.last_equity else equity
        return {
            "account_number": acct.account_number,
            "status": str(acct.status).split('.')[-1],
            "equity": round(equity, 2),
            "cash": round(cash, 2),
            "buying_power": round(buying_power, 2),
            "portfolio_value": round(equity, 2),
            "last_equity": round(last_equity, 2),
            "currency": str(acct.currency).split('.')[-1] if acct.currency else "USD",
            "paper": True,
            "broker": "alpaca",
            "total_pl": round(equity - last_equity, 2),
            "total_pl_pct": round((equity - last_equity) / last_equity * 100, 2) if last_equity > 0 else 0,
            "pattern_day_trader": bool(acct.pattern_day_trader),
            "trading_blocked": bool(acct.trading_blocked),
            "account_blocked": bool(acct.account_blocked),
            "crypto_status": str(getattr(acct, "crypto_status", "ACTIVE")).split('.')[-1],
        }

    # ── ACCOUNT ─────────────────────────────────────────────────────────
    def get_account(self):
        acct = self._trading.get_account()
        return self._format_account(acct)

    # ── POSITIONS ───────────────────────────────────────────────────────
    def get_positions(self):
        positions = self._trading.get_all_positions()
        out = []
        for p in positions:
            qty = float(p.qty)
            avg_entry = float(p.avg_entry_price)
            current_price = float(p.current_price)
            market_value = float(p.market_value)
            cost_basis = float(p.cost_basis)
            unrealized_pl = float(p.unrealized_pl)
            unrealized_plpc = float(p.unrealized_plpc) * 100 if p.unrealized_plpc else 0
            out.append({
                "symbol": p.symbol,
                "qty": qty,
                "avg_entry_price": round(avg_entry, 4),
                "cost_basis": round(cost_basis, 2),
                "current_price": round(current_price, 4),
                "market_value": round(market_value, 2),
                "unrealized_pl": round(unrealized_pl, 2),
                "unrealized_plpc": round(unrealized_plpc, 2),
                "side": str(p.side).split('.')[-1].lower() if p.side else "long",
                "asset_class": str(p.asset_class).split('.')[-1].lower() if p.asset_class else "crypto",
                "exchange": str(p.exchange) if p.exchange else "",
            })
        return out

    def get_position(self, symbol):
        try:
            p = self._trading.get_open_position(symbol)
            qty = float(p.qty)
            avg_entry = float(p.avg_entry_price)
            current_price = float(p.current_price)
            market_value = float(p.market_value)
            cost_basis = float(p.cost_basis)
            unrealized_pl = float(p.unrealized_pl)
            unrealized_plpc = float(p.unrealized_plpc) * 100 if p.unrealized_plpc else 0
            return {
                "symbol": p.symbol,
                "qty": qty,
                "avg_entry_price": round(avg_entry, 4),
                "cost_basis": round(cost_basis, 2),
                "current_price": round(current_price, 4),
                "market_value": round(market_value, 2),
                "unrealized_pl": round(unrealized_pl, 2),
                "unrealized_plpc": round(unrealized_plpc, 2),
                "side": str(p.side).lower() if p.side else "long",
            }
        except Exception:
            return None

    # ── ORDERS ──────────────────────────────────────────────────────────
    def submit_order(self, symbol, notional_usd, side="buy"):
        """Submit a market order by notional (dollar amount)."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

        req = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional_usd, 2),
            side=order_side,
            time_in_force=TimeInForce.GTC,
        )
        order = self._trading.submit_order(req)
        return self._format_order(order)

    def _format_order(self, o):
        return {
            "id": str(o.id),
            "symbol": o.symbol,
            "side": str(o.side).split('.')[-1].lower(),
            "type": str(o.type).split('.')[-1].lower() if o.type else "market",
            "notional": float(o.notional) if o.notional else 0,
            "qty": float(o.qty) if o.qty else 0,
            "filled_qty": float(o.filled_qty) if o.filled_qty else 0,
            "filled_avg_price": float(o.filled_avg_price) if o.filled_avg_price else 0,
            "status": str(o.status).split('.')[-1].lower(),
            "submitted_at": o.submitted_at.isoformat() if o.submitted_at else "",
            "filled_at": o.filled_at.isoformat() if o.filled_at else "",
            "created_at": o.created_at.isoformat() if o.created_at else "",
        }

    def get_orders(self, limit=50, status="all"):
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        status_map = {
            "all": QueryOrderStatus.ALL,
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
        }
        req = GetOrdersRequest(
            status=status_map.get(status, QueryOrderStatus.ALL),
            limit=limit,
        )
        orders = self._trading.get_orders(req)
        return [self._format_order(o) for o in orders]

    def get_daily_pnl(self, period="1A"):
        """Return daily equity snapshots suitable for the dashboard calendar."""
        from alpaca.trading.requests import GetPortfolioHistoryRequest

        history = self._trading.get_portfolio_history(
            GetPortfolioHistoryRequest(period=period, timeframe="1D")
        )

        timestamps = list(getattr(history, "timestamp", []) or [])
        equities = list(getattr(history, "equity", []) or [])
        profit_loss = list(getattr(history, "profit_loss", []) or [])
        profit_loss_pct = list(getattr(history, "profit_loss_pct", []) or [])
        base_value = float(getattr(history, "base_value", 0) or 0)

        snapshots = {}
        prev_equity = None
        for idx, ts in enumerate(timestamps):
            try:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            except Exception:
                continue

            date_key = dt.strftime("%Y-%m-%d")
            equity = float(equities[idx]) if idx < len(equities) and equities[idx] is not None else 0.0
            total_pl = float(profit_loss[idx]) if idx < len(profit_loss) and profit_loss[idx] is not None else 0.0
            total_pl_pct = float(profit_loss_pct[idx]) * 100 if idx < len(profit_loss_pct) and profit_loss_pct[idx] is not None else 0.0
            day_pl = 0.0 if prev_equity is None else equity - prev_equity
            day_pl_pct = 0.0 if prev_equity in (None, 0) else (day_pl / prev_equity) * 100.0

            snapshots[date_key] = {
                "date": date_key,
                "recorded_at": dt.isoformat(),
                "equity": round(equity, 2),
                "starting_balance": round(base_value, 2),
                "total_pl": round(total_pl, 2),
                "total_pl_pct": round(total_pl_pct, 2),
                "day_pl": round(day_pl, 2),
                "day_pl_pct": round(day_pl_pct, 2),
                "source": "alpaca",
            }
            prev_equity = equity

        return snapshots

    # ── CLOSE ───────────────────────────────────────────────────────────
    def close_position(self, symbol):
        try:
            result = self._trading.close_position(symbol)
            return self._format_order(result)
        except Exception as e:
            return {"error": str(e), "symbol": symbol}

    def close_all_positions(self):
        try:
            results = self._trading.close_all_positions(cancel_orders=True)
            return [{"symbol": str(r), "status": "closing"} for r in results]
        except Exception as e:
            return [{"error": str(e)}]

    # ── LATEST PRICE ────────────────────────────────────────────────────
    def get_latest_price(self, symbol):
        """Get latest crypto price from Alpaca data API."""
        try:
            from alpaca.data.requests import CryptoLatestQuoteRequest
            data_client = _get_data_client()
            req = CryptoLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = data_client.get_crypto_latest_quote(req)
            if symbol in quotes:
                return float(quotes[symbol].ask_price)
        except Exception as e:
            logger.warning(f"Could not get price for {symbol}: {e}")
        return None
