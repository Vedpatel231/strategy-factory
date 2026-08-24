"""
Options wheel engine (Stage 2) — decision logic for a cash-secured put seller.

Pure decisions: given current positions, option-chain data, and config, it
returns a list of actions (sell_put / close_put / sell_call / close_call /
hold). Execution is handled separately (Stage 3), so this module is fully
unit-testable without touching Alpaca.

Wheel state machine, per underlying:
  FLAT           -> sell a ~target-delta put (if IV rich enough, under max_positions)
  SHORT_PUT      -> buy-to-close at profit-take %, else hold (let it expire / assign)
  HOLDING_SHARES -> sell a ~target-delta covered call at/above cost basis
  SHORT_CALL     -> buy-to-close at profit-take %, else hold (let it get called away)
"""

import datetime as dt

import config
from options_data import parse_occ_symbol, pick_by_delta


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def engine_config():
    return {
        "target_delta": config.OPT_TARGET_DELTA,
        "delta_tol": config.OPT_DELTA_TOLERANCE,
        "min_dte": config.OPT_MIN_DTE,
        "max_dte": config.OPT_MAX_DTE,
        "target_dte": getattr(config, "OPT_TARGET_DTE", 9),
        "profit_take": config.OPT_PROFIT_TAKE_PCT,
        "min_iv": config.OPT_MIN_IV_PCT,
        "max_positions": config.OPT_MAX_POSITIONS,
        "cc_delta": config.OPT_COVERED_CALL_DELTA,
    }


def classify_positions(positions):
    """Group Alpaca positions by underlying into wheel state.

    Each position dict: symbol, qty, avg_entry_price (equity or OCC option).
    Returns {underlying: {short_puts:[...], short_calls:[...], shares, share_cost}}.
    """
    state = {}

    def ensure(u):
        return state.setdefault(u, {"short_puts": [], "short_calls": [],
                                    "shares": 0.0, "share_cost": 0.0})

    for p in positions or []:
        sym = str(p.get("symbol") or "")
        qty = _num(p.get("qty"))
        avg = _num(p.get("avg_entry_price") or p.get("avg_price"))
        info = parse_occ_symbol(sym)
        if info:  # option contract
            st = ensure(info["root"])
            leg = {"contract": sym, "type": info["type"], "strike": info["strike"],
                   "expiration": info["expiration"], "qty": qty, "entry": avg}
            if info["type"] == "put" and qty < 0:
                st["short_puts"].append(leg)
            elif info["type"] == "call" and qty < 0:
                st["short_calls"].append(leg)
        else:  # equity
            st = ensure(sym.upper())
            st["shares"] += qty
            if avg:
                st["share_cost"] = avg
    return state


def _pick_option(chain, key, target_delta, cfg):
    """Pick the single expiration nearest the target DTE (within the min/max
    window), then the strike closest to target delta within that expiration.
    Selecting one expiration avoids mixing weeklies of different lengths."""
    if not chain or chain.get("error"):
        return None
    exps = [e for e in (chain.get("expirations") or [])
            if e.get("dte") is not None and cfg["min_dte"] <= e["dte"] <= cfg["max_dte"]]
    if not exps:
        return None
    target = cfg.get("target_dte", 9)
    exps.sort(key=lambda e: (abs(e["dte"] - target), e["dte"]))
    chosen = exps[0]
    return pick_by_delta(chosen.get(key) or [], target_delta, cfg["delta_tol"])


def decide_for_underlying(u, st, put_chain_fn, call_chain_fn, quote_fn, cfg,
                          buying_power, open_put_count):
    short_puts = st["short_puts"]
    short_calls = st["short_calls"]
    shares = st["shares"]

    # 1) Manage an existing short put.
    if short_puts:
        leg = short_puts[0]
        cur = quote_fn(leg["contract"])
        entry = abs(_num(leg["entry"]))
        if cur is None or entry <= 0:
            return {"action": "hold", "symbol": u, "contract": leg["contract"],
                    "reason": "no quote / unknown entry on open put"}
        profit_pct = (entry - cur) / entry  # short put profits as price falls
        if profit_pct >= cfg["profit_take"]:
            return {"action": "close_put", "symbol": u, "contract": leg["contract"],
                    "limit_price": round(cur, 2), "profit_pct": round(profit_pct, 3),
                    "reason": f"take profit {profit_pct * 100:.0f}% of credit"}
        return {"action": "hold", "symbol": u, "contract": leg["contract"],
                "profit_pct": round(profit_pct, 3),
                "reason": "short put open, below profit target"}

    # 2) Assigned into shares, no covered call yet -> sell one.
    if shares >= 100 and not short_calls:
        cc = call_chain_fn(u)
        pick = _pick_option(cc, "calls", cfg["cc_delta"], cfg)
        if not pick or pick.get("mid") is None:
            return {"action": "hold", "symbol": u, "reason": "no suitable covered call"}
        if pick["strike"] < st["share_cost"]:
            return {"action": "hold", "symbol": u,
                    "reason": (f"best call strike {pick['strike']} below cost basis "
                               f"{st['share_cost']:.2f} — would lock a loss")}
        return {"action": "sell_call", "symbol": u, "contract": pick["symbol"],
                "strike": pick["strike"], "expiration": pick["expiration"],
                "limit_price": pick["mid"], "delta": pick.get("delta"),
                "reason": "covered call (wheel step 2)"}

    # 3) Manage an existing short (covered) call.
    if short_calls:
        leg = short_calls[0]
        cur = quote_fn(leg["contract"])
        entry = abs(_num(leg["entry"]))
        if cur is None or entry <= 0:
            return {"action": "hold", "symbol": u, "contract": leg["contract"],
                    "reason": "no quote / unknown entry on open call"}
        profit_pct = (entry - cur) / entry
        if profit_pct >= cfg["profit_take"]:
            return {"action": "close_call", "symbol": u, "contract": leg["contract"],
                    "limit_price": round(cur, 2), "profit_pct": round(profit_pct, 3),
                    "reason": f"take profit {profit_pct * 100:.0f}% of credit"}
        return {"action": "hold", "symbol": u, "contract": leg["contract"],
                "reason": "covered call open, below profit target"}

    # 4) Flat -> consider selling a new put.
    if open_put_count >= cfg["max_positions"]:
        return {"action": "hold", "symbol": u, "reason": "max concurrent puts reached"}
    pc = put_chain_fn(u)
    if not pc or pc.get("error"):
        return {"action": "hold", "symbol": u, "reason": "no put chain"}
    pick = _pick_option(pc, "puts", cfg["target_delta"], cfg)
    if not pick or pick.get("mid") is None:
        return {"action": "hold", "symbol": u,
                "reason": "no put near target delta in DTE window"}
    iv = pick.get("iv_pct")
    if iv is not None and iv < cfg["min_iv"]:
        return {"action": "hold", "symbol": u,
                "reason": f"IV {iv}% below floor {cfg['min_iv']}%"}
    collateral = pick["strike"] * 100
    if buying_power < collateral:
        return {"action": "hold", "symbol": u,
                "reason": f"insufficient buying power for ${collateral:.0f} collateral"}
    return {"action": "sell_put", "symbol": u, "contract": pick["symbol"],
            "strike": pick["strike"], "expiration": pick["expiration"],
            "limit_price": pick["mid"], "delta": pick.get("delta"), "iv_pct": iv,
            "credit_est": round((pick["mid"] or 0) * 100, 2),
            "collateral": round(collateral, 2),
            "reason": f"sell ~{cfg['target_delta']:.2f}-delta cash-secured put"}


def decide_actions(underlyings, positions, put_chain_fn, call_chain_fn, quote_fn,
                   buying_power, cfg=None):
    """Return the list of wheel actions for all underlyings."""
    cfg = cfg or engine_config()
    state = classify_positions(positions)
    open_put_count = sum(len(s["short_puts"]) for s in state.values())
    actions = []
    for u in underlyings:
        st = state.get(u, {"short_puts": [], "short_calls": [], "shares": 0.0, "share_cost": 0.0})
        act = decide_for_underlying(u, st, put_chain_fn, call_chain_fn, quote_fn,
                                    cfg, buying_power, open_put_count)
        if act:
            actions.append(act)
            if act.get("action") == "sell_put":
                open_put_count += 1
    return actions
