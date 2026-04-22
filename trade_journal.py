"""
Persistent trade and decision journal.

The dashboard and learning layer should treat Alpaca paper fills as the source
of real paper-trading truth. Seeded/backtest metrics stay separate.
"""

import json
import os
from datetime import datetime, timezone

import config

JOURNAL_FILE = os.path.join(config.DATA_DIR, "trade_journal.json")
POSITION_STATE_FILE = os.path.join(config.DATA_DIR, "position_risk_state.json")
ALPACA_CRYPTO_MAKER_FEE_BPS = float(os.environ.get("ALPACA_CRYPTO_MAKER_FEE_BPS", "15"))
ALPACA_CRYPTO_TAKER_FEE_BPS = float(os.environ.get("ALPACA_CRYPTO_TAKER_FEE_BPS", "25"))


def _utcnow():
    return datetime.now(timezone.utc).isoformat()


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


class TradeJournal:
    def __init__(self, journal_file=JOURNAL_FILE):
        self.journal_file = journal_file

    def append(self, event):
        events = _read_json(self.journal_file, [])
        if not isinstance(events, list):
            events = []
        event = dict(event)
        event.setdefault("timestamp", _utcnow())
        events.append(event)
        _write_json(self.journal_file, events[-2000:])
        return event

    def recent(self, limit=200):
        events = _read_json(self.journal_file, [])
        if not isinstance(events, list):
            return []
        return list(reversed(events[-limit:]))


class PositionRiskBook:
    def __init__(self, state_file=POSITION_STATE_FILE):
        self.state_file = state_file
        self.state = _read_json(state_file, {})
        if not isinstance(self.state, dict):
            self.state = {}

    def save(self):
        _write_json(self.state_file, self.state)

    def register_entry(
        self,
        symbol,
        strategy,
        regime,
        confidence,
        entry_price,
        notional,
        stop_loss_pct,
        take_profit_pct,
        trailing_stop_pct,
        max_hold_hours,
        reason,
        bot_names=None,
    ):
        existing = self.state.get(symbol, {})
        high_water = max(float(existing.get("high_water_price", 0) or 0), float(entry_price or 0))
        self.state[symbol] = {
            "symbol": symbol,
            "strategy": strategy,
            "regime": regime,
            "confidence": confidence,
            "entry_price": entry_price,
            "entry_notional": notional,
            "opened_at": existing.get("opened_at") or _utcnow(),
            "updated_at": _utcnow(),
            "high_water_price": high_water,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
            "max_hold_hours": max_hold_hours,
            "entry_reason": reason,
            "bot_names": list(bot_names or []),
        }
        self.save()
        return self.state[symbol]

    def remove(self, symbol):
        entry = self.state.pop(symbol, None)
        self.save()
        return entry

    def update_high_water(self, symbol, current_price):
        if symbol not in self.state:
            return None
        entry = self.state[symbol]
        entry["high_water_price"] = max(
            float(entry.get("high_water_price", 0) or 0),
            float(current_price or 0),
        )
        entry["updated_at"] = _utcnow()
        self.save()
        return entry

    def get(self, symbol):
        return self.state.get(symbol)

    def all(self):
        return dict(self.state)


def load_trade_journal(limit=200):
    return TradeJournal().recent(limit=limit)


def load_position_risk_state():
    return PositionRiskBook().all()


def alpaca_fee_config():
    return {
        "maker_bps": ALPACA_CRYPTO_MAKER_FEE_BPS,
        "taker_bps": ALPACA_CRYPTO_TAKER_FEE_BPS,
        "default_order_type": os.environ.get("ALPACA_FEE_ORDER_TYPE", "taker").lower(),
        "source": "estimated_alpaca_crypto_fee_model",
    }


def estimate_alpaca_fee(notional, order_type=None):
    cfg = alpaca_fee_config()
    fee_bps = cfg["maker_bps"] if (order_type or cfg["default_order_type"]) == "maker" else cfg["taker_bps"]
    return float(notional or 0) * fee_bps / 10000.0


def _as_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_money(value):
    return round(_as_float(value), 2)


def summarize_fee_analysis(limit=2000, open_positions=None, risk_state=None):
    """
    Return fee-aware realized and open-trade performance.

    Alpaca paper does not charge real fees. This estimates live Alpaca crypto
    fees so paper performance is reviewed with realistic cost drag.
    """
    events = list(reversed(TradeJournal().recent(limit=limit)))
    cfg = alpaca_fee_config()
    order_type = cfg["default_order_type"]
    fallback_entries = {}
    closed = []

    for event in events:
        event_type = event.get("event")
        symbol = event.get("symbol")
        if not symbol:
            continue

        if event_type == "order_submitted" and event.get("side") == "buy":
            order = event.get("order") or {}
            fallback_entries[symbol] = {
                "symbol": symbol,
                "strategy": event.get("strategy"),
                "regime": event.get("regime"),
                "confidence": event.get("confidence"),
                "entry_price": order.get("filled_avg_price") or 0,
                "entry_notional": event.get("notional") or order.get("notional") or 0,
                "opened_at": event.get("timestamp"),
                "entry_reason": event.get("entry_reason"),
                "bot_names": event.get("bot_names") or [],
            }
            continue

        if event_type != "position_closed":
            continue

        entry = event.get("entry_state") or fallback_entries.get(symbol) or {}
        order = event.get("order") or {}
        entry_notional = _as_float(entry.get("entry_notional") or entry.get("notional"))
        entry_price = _as_float(entry.get("entry_price") or order.get("entry_price"))
        exit_notional = _as_float(event.get("exit_notional") or order.get("notional"))
        exit_price = _as_float(event.get("exit_price") or order.get("filled_avg_price"))
        pl_pct = _as_float(event.get("unrealized_pl_pct"))

        if not exit_notional and entry_notional:
            exit_notional = entry_notional * (1 + pl_pct / 100.0)
        if not exit_price and entry_price:
            exit_price = entry_price * (1 + pl_pct / 100.0)
        if not entry_notional and exit_notional and pl_pct > -99:
            entry_notional = exit_notional / (1 + pl_pct / 100.0)

        gross_pl = exit_notional - entry_notional
        entry_fee = estimate_alpaca_fee(entry_notional, order_type)
        exit_fee = estimate_alpaca_fee(exit_notional, order_type)
        total_fees = entry_fee + exit_fee
        net_pl = gross_pl - total_fees

        closed.append({
            "timestamp": event.get("timestamp"),
            "symbol": symbol,
            "strategy": entry.get("strategy") or event.get("strategy") or "unknown",
            "regime": entry.get("regime") or event.get("regime") or "unknown",
            "entry_price": round(entry_price, 6) if entry_price else 0,
            "exit_price": round(exit_price, 6) if exit_price else 0,
            "entry_notional": _round_money(entry_notional),
            "exit_notional": _round_money(exit_notional),
            "gross_pl": _round_money(gross_pl),
            "entry_fee": _round_money(entry_fee),
            "exit_fee": _round_money(exit_fee),
            "total_fees": _round_money(total_fees),
            "net_pl": _round_money(net_pl),
            "gross_pl_pct": round((gross_pl / entry_notional) * 100, 2) if entry_notional else 0,
            "net_pl_pct": round((net_pl / entry_notional) * 100, 2) if entry_notional else 0,
            "fee_drag_pct": round((total_fees / entry_notional) * 100, 2) if entry_notional else 0,
            "exit_reason": event.get("reason", ""),
            "order_type": order_type,
        })

    open_rows = []
    risk_state = risk_state or PositionRiskBook().all()
    for pos in open_positions or []:
        symbol = pos.get("symbol")
        state = risk_state.get(symbol, {}) if isinstance(risk_state, dict) else {}
        entry_notional = _as_float(state.get("entry_notional") or pos.get("cost_basis"))
        exit_notional = _as_float(pos.get("market_value"))
        entry_price = _as_float(state.get("entry_price") or pos.get("avg_entry_price"))
        current_price = _as_float(pos.get("current_price"))
        gross_pl = exit_notional - entry_notional
        entry_fee = estimate_alpaca_fee(entry_notional, order_type)
        exit_fee = estimate_alpaca_fee(exit_notional, order_type)
        total_fees = entry_fee + exit_fee
        net_pl = gross_pl - total_fees
        open_rows.append({
            "symbol": symbol,
            "strategy": state.get("strategy") or "manual/legacy",
            "regime": state.get("regime") or "unknown",
            "entry_price": round(entry_price, 6) if entry_price else 0,
            "current_price": round(current_price, 6) if current_price else 0,
            "entry_notional": _round_money(entry_notional),
            "mark_notional": _round_money(exit_notional),
            "gross_pl_if_closed": _round_money(gross_pl),
            "estimated_round_trip_fees": _round_money(total_fees),
            "net_pl_if_closed": _round_money(net_pl),
            "net_pl_pct_if_closed": round((net_pl / entry_notional) * 100, 2) if entry_notional else 0,
            "fee_drag_pct": round((total_fees / entry_notional) * 100, 2) if entry_notional else 0,
            "order_type": order_type,
        })

    total_gross = sum(_as_float(r["gross_pl"]) for r in closed)
    total_fees = sum(_as_float(r["total_fees"]) for r in closed)
    total_net = sum(_as_float(r["net_pl"]) for r in closed)
    wins_net = sum(1 for r in closed if _as_float(r["net_pl"]) > 0)
    open_net = sum(_as_float(r["net_pl_if_closed"]) for r in open_rows)
    open_fees = sum(_as_float(r["estimated_round_trip_fees"]) for r in open_rows)

    return {
        "fee_config": cfg,
        "summary": {
            "closed_trades": len(closed),
            "net_wins": wins_net,
            "net_win_rate": round(wins_net / len(closed) * 100, 1) if closed else None,
            "realized_gross_pl": _round_money(total_gross),
            "realized_estimated_fees": _round_money(total_fees),
            "realized_net_pl": _round_money(total_net),
            "fee_drag_vs_gross_pct": round((total_fees / abs(total_gross)) * 100, 1) if total_gross else None,
            "open_net_pl_if_closed": _round_money(open_net),
            "open_estimated_round_trip_fees": _round_money(open_fees),
        },
        "closed_trades": list(reversed(closed[-100:])),
        "open_trades": open_rows,
    }


def summarize_real_paper_performance(limit=2000):
    """
    Summarize real Alpaca paper-trading outcomes by bot name.

    This intentionally ignores seeded/backtest metrics. It uses journaled paper
    order/exit events only. Scores remain neutral until enough closed trades
    exist, so the dashboard does not pretend that missing data is evidence.
    """
    events = TradeJournal().recent(limit=limit)
    by_bot = {}

    def ensure(bot_name):
        if bot_name not in by_bot:
            by_bot[bot_name] = {
                "entries": 0,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "total_pl_pct": 0.0,
                "avg_pl_pct": 0.0,
                "score": None,
                "label": "NO_REAL_DATA",
                "source": "alpaca_paper_journal",
            }
        return by_bot[bot_name]

    for event in reversed(events):
        event_type = event.get("event")
        if event_type == "order_submitted" and event.get("side") == "buy":
            for bot_name in event.get("bot_names", []) or []:
                ensure(bot_name)["entries"] += 1
            continue

        if event_type != "position_closed":
            continue

        entry_state = event.get("entry_state") or {}
        bot_names = entry_state.get("bot_names") or event.get("bot_names") or []
        try:
            pl_pct = float(event.get("unrealized_pl_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            pl_pct = 0.0

        for bot_name in bot_names:
            row = ensure(bot_name)
            row["closed_trades"] += 1
            row["total_pl_pct"] += pl_pct
            if pl_pct > 0:
                row["wins"] += 1
            else:
                row["losses"] += 1

    for row in by_bot.values():
        closed = row["closed_trades"]
        if closed <= 0:
            continue
        win_rate = row["wins"] / closed
        avg_pl = row["total_pl_pct"] / closed
        row["avg_pl_pct"] = round(avg_pl, 2)

        # Neutral baseline 50. Reward win rate and average realized/exit P&L,
        # penalize thin samples so 1 lucky trade does not look like skill.
        sample_conf = min(1.0, closed / 10.0)
        raw_score = 50 + ((win_rate - 0.5) * 45) + (avg_pl * 4)
        raw_score = 50 + ((raw_score - 50) * sample_conf)
        score = max(0, min(100, round(raw_score, 1)))
        row["score"] = score
        row["win_rate"] = round(win_rate * 100, 1)

        if closed < 3:
            row["label"] = "TOO_FEW_REAL_TRADES"
        elif score >= 70:
            row["label"] = "REAL_PAPER_STRONG"
        elif score >= 55:
            row["label"] = "REAL_PAPER_OK"
        elif score >= 40:
            row["label"] = "REAL_PAPER_WEAK"
        else:
            row["label"] = "REAL_PAPER_POOR"

    return by_bot
