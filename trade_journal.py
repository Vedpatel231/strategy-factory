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
