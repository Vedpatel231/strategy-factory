"""Persistent decision log for the Strategy Factory trading desk."""

import json
import os
from datetime import datetime, timezone

import config


TRADING_DESK_STATE_FILE = os.path.join(config.DATA_DIR, "trading_desk_state.json")
TRADING_DESK_DECISIONS_FILE = os.path.join(config.DATA_DIR, "trading_desk_decisions.json")


def utc_now():
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


def load_trading_desk_state():
    return _read_json(TRADING_DESK_STATE_FILE, {})


def load_decision_log(limit=300):
    rows = _read_json(TRADING_DESK_DECISIONS_FILE, [])
    if not isinstance(rows, list):
        return []
    return list(reversed(rows[-limit:]))


class DecisionLogger:
    def __init__(self, state_file=TRADING_DESK_STATE_FILE, decisions_file=TRADING_DESK_DECISIONS_FILE):
        self.state_file = state_file
        self.decisions_file = decisions_file

    def append(self, event_type, payload):
        rows = _read_json(self.decisions_file, [])
        if not isinstance(rows, list):
            rows = []
        event = dict(payload or {})
        event.setdefault("timestamp", utc_now())
        event["event"] = event_type
        rows.append(event)
        _write_json(self.decisions_file, rows[-3000:])
        return event

    def save_cycle_state(self, state):
        state = dict(state or {})
        state.setdefault("updated_at", utc_now())
        state["decision_log"] = load_decision_log(limit=200)
        _write_json(self.state_file, state)
        return state
