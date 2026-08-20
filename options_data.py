"""
Options data layer (Phase 1 of the options build).

Read-only helpers to fetch a live option chain from Alpaca and shape it into a
compact, beginner-friendly view: for the nearest expirations, the puts around
the current price with bid/ask/mid, implied volatility, delta, and break-even.

Uses the account's own Alpaca keys. No orders, no state writes.
"""

import re
import datetime as dt

_OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def parse_occ_symbol(sym):
    """Parse an OCC option symbol, e.g. 'AAPL260828P00310000'."""
    m = _OCC.match(str(sym or "").strip())
    if not m:
        return None
    root, ymd, cp, strike = m.groups()
    try:
        exp = dt.date(2000 + int(ymd[0:2]), int(ymd[2:4]), int(ymd[4:6])).isoformat()
    except ValueError:
        return None
    return {"root": root, "expiration": exp,
            "type": "put" if cp == "P" else "call",
            "strike": int(strike) / 1000.0}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _snapshot_fields(snap):
    """Pull bid/ask/iv/delta from an Alpaca OptionsSnapshot defensively."""
    bid = ask = iv = delta = None
    q = getattr(snap, "latest_quote", None)
    if q is not None:
        bid = _num(getattr(q, "bid_price", None))
        ask = _num(getattr(q, "ask_price", None))
    iv = _num(getattr(snap, "implied_volatility", None))
    g = getattr(snap, "greeks", None)
    if g is not None:
        delta = _num(getattr(g, "delta", None))
    return bid, ask, iv, delta


def shape_put_chain(chain, spot, max_expirations=2, strikes_per_exp=6,
                    strike_window=0.10, max_dte=60):
    """Turn a raw chain dict {occ_symbol: snapshot} into a compact put view.

    Keeps puts within +/- strike_window of spot, groups by expiration, keeps the
    nearest `max_expirations`, and `strikes_per_exp` strikes closest to spot.
    """
    today = dt.date.today()
    lo = spot * (1 - strike_window) if spot else None
    hi = spot * (1 + 0.03) if spot else None  # a little above spot is enough for puts
    rows = []
    for occ, snap in (chain or {}).items():
        info = parse_occ_symbol(occ)
        if not info or info["type"] != "put":
            continue
        strike = info["strike"]
        if lo is not None and (strike < lo or strike > hi):
            continue
        try:
            exp_date = dt.date.fromisoformat(info["expiration"])
        except ValueError:
            continue
        dte = (exp_date - today).days
        if dte < 0 or dte > max_dte:
            continue
        bid, ask, iv, delta = _snapshot_fields(snap)
        mid = round((bid + ask) / 2, 2) if (bid is not None and ask is not None) else None
        rows.append({
            "symbol": occ,
            "expiration": info["expiration"],
            "dte": dte,
            "strike": round(strike, 2),
            "bid": bid, "ask": ask, "mid": mid,
            "iv_pct": round(iv * 100, 1) if iv is not None else None,
            "delta": round(delta, 3) if delta is not None else None,
            "breakeven": round(strike - mid, 2) if mid is not None else None,
        })

    by_exp = {}
    for r in rows:
        by_exp.setdefault(r["expiration"], []).append(r)
    exps = sorted(by_exp.keys())[:max_expirations]
    out = []
    for e in exps:
        puts = by_exp[e]
        # keep the strikes closest to spot
        puts.sort(key=lambda r: abs(r["strike"] - spot) if spot else r["strike"])
        keep = sorted(puts[:strikes_per_exp], key=lambda r: -r["strike"])
        out.append({"expiration": e, "dte": keep[0]["dte"] if keep else None, "puts": keep})
    return out


def get_live_put_chain(symbol, api_key, api_secret,
                       max_expirations=2, strike_window=0.10, max_dte=60):
    """Fetch and shape a live put chain. Returns dict with spot + expirations.

    Defensive about alpaca-py version differences; returns {'error': ...} on
    failure with a hint about which step failed.
    """
    symbol = str(symbol or "AAPL").upper()

    # 1) spot price
    spot = None
    try:
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest
        sc = StockHistoricalDataClient(api_key=api_key, secret_key=api_secret)
        lt = sc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol))
        spot = _num(getattr(lt.get(symbol), "price", None))
    except Exception as e:
        return {"error": f"spot price fetch failed: {e}", "step": "spot"}

    # 2) option chain (filter to puts + strike/expiration window when supported)
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest
        oc = OptionHistoricalDataClient(api_key=api_key, secret_key=api_secret)
        kwargs = {"underlying_symbol": symbol}
        try:
            from alpaca.trading.enums import ContractType
            kwargs["type"] = ContractType.PUT
        except Exception:
            pass
        if spot:
            kwargs["strike_price_gte"] = round(spot * (1 - strike_window), 2)
            kwargs["strike_price_lte"] = round(spot * 1.03, 2)
        try:
            chain = oc.get_option_chain(OptionChainRequest(**kwargs))
        except TypeError:
            chain = oc.get_option_chain(OptionChainRequest(underlying_symbol=symbol))
    except Exception as e:
        return {"error": f"option chain fetch failed: {e}", "step": "chain", "spot": spot}

    try:
        expirations = shape_put_chain(chain, spot, max_expirations=max_expirations,
                                      strike_window=strike_window, max_dte=max_dte)
    except Exception as e:
        return {"error": f"chain parse failed: {e}", "step": "parse", "spot": spot}

    return {"symbol": symbol, "spot": round(spot, 2) if spot else None,
            "expirations": expirations,
            "contracts_returned": len(chain or {})}
