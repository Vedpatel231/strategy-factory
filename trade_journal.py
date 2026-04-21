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
