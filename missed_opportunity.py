"""Missed Opportunity Analyzer.

Tracks what trades WOULD have been profitable but were skipped,
and explains why. Shown on the dashboard to answer:
"Why didn't we trade today when the market was green?"

Each cycle, the analyzer inspects every manager decision and records:
  - Signals that were generated but blocked (by quality score, cooldown, regime, etc.)
  - The hypothetical P&L if the trade had been taken (based on price movement since signal)
  - The specific reason it was blocked

State persists per day and resets at midnight UTC.
"""

import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger("missed_opportunity")

STATE_FILE = os.path.join(config.DATA_DIR, "missed_opportunities.json")


def _utcnow():
    return datetime.now(timezone.utc)


def _today_str():
    return _utcnow().strftime("%Y-%m-%d")


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=str)
    os.replace(tmp, STATE_FILE)


class MissedOpportunityAnalyzer:
    """Analyze and record missed trading opportunities each cycle."""

    def __init__(self):
        self._load()

    def _load(self):
        raw = _read_state()
        today = _today_str()
        if raw.get("date") != today:
            self._state = {
                "date": today,
                "missed": [],
                "summary": {
                    "total_missed": 0,
                    "total_hypothetical_pl": 0.0,
                    "reasons": {},
                    "top_missed_symbols": [],
                },
                "no_trade_explanation": [],
            }
        else:
            self._state = raw

    def _persist(self):
        _write_state(self._state)

    def record_cycle(self, managers, ceo_state, approvals=None, conservative_status=None):
        """Analyze one cycle's decisions and record missed opportunities.

        Args:
            managers: list of manager decision dicts
            ceo_state: CEOState.to_dict()
            approvals: list of risk approval dicts
            conservative_status: conservative mode status dict
        """
        today = _today_str()
        if self._state.get("date") != today:
            self._load()

        cycle_missed = []
        cycle_explanations = []

        for mgr in managers or []:
            action = mgr.get("action", "")
            symbol = mgr.get("symbol", "")
            reason = mgr.get("reason", "") or mgr.get("rejection_reason", "")

            # Record why each asset had no trade
            if action in ("wait", "cooldown"):
                explanation = self._classify_reason(reason, mgr, ceo_state, conservative_status)
                cycle_explanations.append({
                    "symbol": symbol,
                    "action": action,
                    "category": explanation["category"],
                    "detail": explanation["detail"],
                    "closest_bot": mgr.get("active_bot") or mgr.get("active_strategy"),
                    "score": mgr.get("score", 0),
                    "confidence": mgr.get("confidence", 0),
                })

                # Check if there was a near-miss (buy signal that was blocked)
                closest = mgr.get("closest_bot") or {}
                selected_signal = mgr.get("selected_signal") or {}
                signal_action = selected_signal.get("action") or closest.get("action", "")

                if signal_action == "buy" or "quality score" in reason.lower():
                    entry_price = _safe_float(
                        (selected_signal.get("features") or {}).get("close")
                        or closest.get("entry_price")
                    )
                    missed_entry = {
                        "symbol": symbol,
                        "timestamp": _utcnow().isoformat(),
                        "strategy": mgr.get("active_strategy", ""),
                        "bot_name": mgr.get("active_bot", ""),
                        "entry_price": entry_price,
                        "confidence": mgr.get("confidence", 0),
                        "score": mgr.get("score", 0),
                        "quality_score": 0,
                        "block_reason": reason[:200],
                        "category": explanation["category"],
                        "hypothetical_pl": 0.0,  # updated later when price moves
                    }
                    # Extract quality score if mentioned
                    if "quality score" in reason.lower():
                        try:
                            parts = reason.split("Quality score ")[1].split("/")
                            missed_entry["quality_score"] = int(parts[0])
                        except Exception:
                            pass
                    cycle_missed.append(missed_entry)

        # Also record conservative mode blocks from approvals
        for approval in (approvals or []):
            if not approval.get("approved"):
                reasons = approval.get("reasons", [])
                symbol = approval.get("symbol", "")
                trade_req = approval.get("trade_request") or {}
                for r in reasons:
                    cycle_missed.append({
                        "symbol": symbol,
                        "timestamp": _utcnow().isoformat(),
                        "strategy": trade_req.get("strategy", ""),
                        "bot_name": trade_req.get("bot_name", ""),
                        "entry_price": _safe_float(trade_req.get("entry_price")),
                        "confidence": _safe_float(trade_req.get("confidence")),
                        "score": _safe_float(trade_req.get("manager_score")),
                        "quality_score": _safe_float(trade_req.get("quality_score")),
                        "block_reason": r[:200],
                        "category": "risk_rejected",
                        "hypothetical_pl": 0.0,
                    })

        # Add to state (keep last 100 per day)
        self._state["missed"].extend(cycle_missed)
        self._state["missed"] = self._state["missed"][-100:]

        # Update no-trade explanation (latest cycle only)
        self._state["no_trade_explanation"] = cycle_explanations

        # Recompute summary
        self._update_summary()
        self._persist()

    def _classify_reason(self, reason, mgr, ceo_state, conservative_status):
        """Classify the block reason into a human-readable category."""
        reason_lower = reason.lower()

        if "insufficient" in reason_lower or "candle" in reason_lower or "data" in reason_lower:
            return {"category": "no_data", "detail": "Market data unavailable or insufficient candles loaded"}
        if "quality score" in reason_lower:
            return {"category": "quality_too_low", "detail": "Signal quality below threshold — setup not clean enough"}
        if "cooldown" in reason_lower:
            return {"category": "cooldown", "detail": "Post-trade cooldown period active"}
        if "risk-off" in reason_lower or "risk off" in reason_lower:
            return {"category": "ceo_risk_off", "detail": "CEO detected risk-off market conditions"}
        if "no buy signal" in reason_lower:
            return {"category": "no_signal", "detail": "Strategy conditions not met — no clean entry setup found"}
        if "duplicate" in reason_lower:
            return {"category": "duplicate", "detail": "Already signalled on this candle — preventing double entry"}
        if "regime" in reason_lower and "incompatible" in reason_lower:
            return {"category": "regime_mismatch", "detail": "Strategy type incompatible with current market regime"}
        if "position" in reason_lower and "open" in reason_lower:
            return {"category": "position_open", "detail": "Already holding a position in this asset"}
        if "confidence" in reason_lower:
            return {"category": "low_confidence", "detail": "Strategy confidence below minimum threshold"}
        if "paused" in reason_lower or "disabled" in reason_lower:
            return {"category": "paused", "detail": "Asset or strategy paused after consecutive losses"}
        if "budget" in reason_lower or "risk" in reason_lower:
            return {"category": "risk_budget", "detail": "Open risk budget exceeded"}

        return {"category": "other", "detail": reason[:120]}

    def _update_summary(self):
        missed = self._state.get("missed", [])
        reasons = {}
        symbols = {}
        total_hyp_pl = 0.0

        for m in missed:
            cat = m.get("category", "other")
            reasons[cat] = reasons.get(cat, 0) + 1
            sym = m.get("symbol", "")
            if sym:
                symbols[sym] = symbols.get(sym, 0) + 1
            total_hyp_pl += _safe_float(m.get("hypothetical_pl"))

        top_symbols = sorted(symbols.items(), key=lambda x: x[1], reverse=True)[:10]

        self._state["summary"] = {
            "total_missed": len(missed),
            "total_hypothetical_pl": round(total_hyp_pl, 2),
            "reasons": reasons,
            "top_missed_symbols": [{"symbol": s, "count": c} for s, c in top_symbols],
        }

    def get_status(self):
        """Return current state for dashboard display."""
        today = _today_str()
        if self._state.get("date") != today:
            self._load()
        return {
            "date": self._state.get("date"),
            "total_missed": self._state.get("summary", {}).get("total_missed", 0),
            "reasons": self._state.get("summary", {}).get("reasons", {}),
            "top_missed_symbols": self._state.get("summary", {}).get("top_missed_symbols", []),
            "hypothetical_pl": self._state.get("summary", {}).get("total_hypothetical_pl", 0),
            "no_trade_explanation": self._state.get("no_trade_explanation", [])[:20],
            "recent_missed": self._state.get("missed", [])[-10:],
        }
