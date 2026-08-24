"""
Options executor (Stage 3) — turns wheel actions into Alpaca orders.

Single-leg limit orders, always Time-in-Force DAY (Alpaca rejects GTC on
options). Defaults to DRY-RUN: it logs exactly what it would submit but places
no order. Live paper execution only happens when constructed with dry_run=False
(driven by config.OPTIONS_LIVE).
"""

import os


class OptionsExecutor:
    def __init__(self, dry_run=True, client=None):
        self.dry_run = dry_run
        self._client = client

    def _trading(self):
        if self._client is not None:
            return self._client
        from alpaca.trading.client import TradingClient
        self._client = TradingClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_API_SECRET", ""),
            paper=True,
        )
        return self._client

    def execute(self, action):
        a = (action or {}).get("action")
        if a in (None, "hold"):
            return {"ok": True, "action": a, "skipped": True}

        # sell_put / sell_call = sell to open; close_put / close_call = buy to close
        side = "sell" if a in ("sell_put", "sell_call") else "buy"
        occ = action.get("contract")
        px = action.get("limit_price")
        if not occ or px is None:
            return {"ok": False, "action": a, "error": "missing contract or limit_price"}

        if self.dry_run:
            return {"ok": True, "dry_run": True, "action": a, "side": side,
                    "contract": occ, "limit_price": round(float(px), 2),
                    "reason": action.get("reason")}

        try:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req = LimitOrderRequest(
                symbol=occ, qty=1,
                side=OrderSide.SELL if side == "sell" else OrderSide.BUY,
                limit_price=round(float(px), 2),
                time_in_force=TimeInForce.DAY,
            )
            o = self._trading().submit_order(req)
            return {"ok": True, "action": a, "side": side, "contract": occ,
                    "limit_price": round(float(px), 2),
                    "order_id": str(getattr(o, "id", "")),
                    "status": str(getattr(o, "status", ""))}
        except Exception as e:
            return {"ok": False, "action": a, "contract": occ, "error": str(e)}
