"""
Rolling 24h live monitor for Alpaca paper trading.

Writes a compact JSON snapshot that summarizes the last 24 hours of actual
activity, exits, fee-aware results, throttles, and learning blocks so the
operator and future decision logic can work from one source of truth.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import config

MONITOR_24H_FILE = os.path.join(config.DATA_DIR, "live_monitor_24h.json")


def _utcnow():
    return datetime.now(timezone.utc)


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


def _parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _in_window(ts, cutoff):
    dt = _parse_ts(ts)
    return bool(dt and dt >= cutoff)


def _reason_bucket(reason):
    reason = str(reason or "")
    if reason.startswith("Stop loss"):
        return "stop_loss"
    if reason.startswith("Take profit"):
        return "take_profit"
    if reason.startswith("Trailing stop"):
        return "trailing_stop"
    if reason.startswith("Regime exit"):
        return "regime_exit"
    if reason.startswith("Early timeout"):
        return "early_timeout"
    if reason.startswith("Timeout exit"):
        return "timeout"
    if reason.startswith("Stale position"):
        return "stale_exit"
    return "other"


def build_live_monitor_snapshot(hours=24):
    from alpaca_auto_trader import LOG_FILE
    from learning_engine import LearningEngine
    from trade_journal import JOURNAL_FILE, load_trade_ledger
    from intraday_engine import load_intraday_state

    now = _utcnow()
    cutoff = now - timedelta(hours=hours)
    today = now.strftime("%Y-%m-%d")
    post_exit_cooldown_hours = float(os.environ.get("POST_EXIT_COOLDOWN_HOURS", "3.0"))
    max_trades_per_symbol = int(os.environ.get("MAX_TRADES_PER_SYMBOL_DAY", "3"))

    journal_events = list(reversed(_read_json(JOURNAL_FILE, [])))
    ledger_rows = load_trade_ledger(limit=2000)
    auto_runs = list(reversed(_read_json(LOG_FILE, [])))
    intraday_state = load_intraday_state()
    learner = LearningEngine()

    recent_events = [e for e in journal_events if _in_window(e.get("timestamp") or e.get("closed_at"), cutoff)]
    recent_buys = [e for e in recent_events if e.get("event") == "order_submitted" and e.get("side") == "buy"]
    recent_closes = [e for e in recent_events if e.get("event") == "position_closed"]
    recent_ledger = [r for r in ledger_rows if _in_window(r.get("closed_at") or r.get("timestamp"), cutoff)]
    recent_runs_24h = [r for r in auto_runs if _in_window(r.get("timestamp"), cutoff)]

    entries_by_symbol = {}
    closes_by_symbol = {}
    cooldown_symbols = {}
    exit_reasons = {}
    for event in recent_buys:
        symbol = event.get("symbol") or "unknown"
        entries_by_symbol[symbol] = entries_by_symbol.get(symbol, 0) + 1
    for event in recent_closes:
        symbol = event.get("symbol") or "unknown"
        closes_by_symbol[symbol] = closes_by_symbol.get(symbol, 0) + 1
        bucket = _reason_bucket(event.get("reason"))
        exit_reasons[bucket] = exit_reasons.get(bucket, 0) + 1
        pl_pct = float(event.get("unrealized_pl_pct", 0) or 0)
        ts = _parse_ts(event.get("timestamp") or event.get("closed_at"))
        if ts and pl_pct < 0:
            hours_ago = (now - ts).total_seconds() / 3600.0
            if hours_ago <= post_exit_cooldown_hours:
                cooldown_symbols[symbol] = round(hours_ago, 2)

    capped_symbols = sorted(
        sym for sym, count in entries_by_symbol.items()
        if sym and count >= max_trades_per_symbol and today == today
    )

    realized_net = round(sum(float(r.get("net_pl", 0) or 0) for r in recent_ledger), 2)
    realized_wins = sum(1 for r in recent_ledger if float(r.get("net_pl", 0) or 0) > 0)
    blocked_pairs = []
    for strategy_id, strategy_state in (learner.state.get("strategies") or {}).items():
        real_regimes = strategy_state.get("real_regime_performance") or {}
        for regime, perf in real_regimes.items():
            blocked, reason = learner.should_block_strategy(strategy_id, regime)
            if blocked:
                blocked_pairs.append({
                    "strategy": strategy_id,
                    "regime": regime,
                    "trades": int(perf.get("trades", 0) or 0),
                    "win_rate": float(perf.get("win_rate", 0) or 0),
                    "pnl": round(float(perf.get("pnl", 0) or 0), 2),
                    "reason": reason,
                })

    signal_rows = list((intraday_state or {}).values())
    tradable_signals = sum(1 for row in signal_rows if row.get("accepted"))
    rejected_signals = len(signal_rows) - tradable_signals
    reject_counts = {}
    for row in signal_rows:
        if row.get("accepted"):
            continue
        reason = str(row.get("reason") or "Unknown skip reason").split(";")[0].strip() or "Unknown skip reason"
        reject_counts[reason] = reject_counts.get(reason, 0) + 1
    top_reject_reason = None
    if reject_counts:
        top_reject_reason = sorted(reject_counts.items(), key=lambda item: item[1], reverse=True)[0][0]

    alerts = []
    if not recent_runs_24h:
        alerts.append("no_auto_cycles_24h")
    if tradable_signals == 0 and len(signal_rows) > 0:
        alerts.append("no_tradable_signals_now")
    if len(cooldown_symbols) >= 3:
        alerts.append("multiple_symbols_in_cooldown")
    if blocked_pairs:
        alerts.append("learning_blocks_active")
    if realized_net < 0:
        alerts.append("net_negative_24h")

    ok_runs = sum(1 for r in recent_runs_24h if r.get("status") == "ok")
    snapshot = {
        "generated_at": now.isoformat(),
        "window_hours": hours,
        "window_start": cutoff.isoformat(),
        "window_end": now.isoformat(),
        "auto_trade": {
            "cycles_24h": len(recent_runs_24h),
            "ok_24h": ok_runs,
            "non_ok_24h": len(recent_runs_24h) - ok_runs,
            "last_status": recent_runs_24h[0]["status"] if recent_runs_24h else None,
        },
        "activity": {
            "entries_24h": len(recent_buys),
            "closes_24h": len(recent_closes),
            "losing_closes_24h": sum(1 for e in recent_closes if float(e.get("unrealized_pl_pct", 0) or 0) < 0),
            "entries_by_symbol_24h": entries_by_symbol,
            "closes_by_symbol_24h": closes_by_symbol,
        },
        "performance": {
            "realized_trades_24h": len(recent_ledger),
            "realized_net_pl_24h": realized_net,
            "realized_win_rate_24h": round(realized_wins / len(recent_ledger) * 100, 1) if recent_ledger else None,
        },
        "risk_controls": {
            "symbols_in_post_exit_cooldown_now": cooldown_symbols,
            "symbols_at_trade_cap_today": capped_symbols,
            "exit_reason_breakdown_24h": exit_reasons,
        },
        "learning": {
            "blocked_pairs_now": blocked_pairs,
            "blocked_pair_count_now": len(blocked_pairs),
        },
        "signal_snapshot": {
            "symbols_checked_now": len(signal_rows),
            "tradable_signals_now": tradable_signals,
            "rejected_signals_now": rejected_signals,
            "top_reject_reason_now": top_reject_reason,
        },
        "alerts": alerts,
    }
    return snapshot


def write_live_monitor_snapshot(hours=24):
    snapshot = build_live_monitor_snapshot(hours=hours)
    _write_json(MONITOR_24H_FILE, snapshot)
    return snapshot


def load_live_monitor_snapshot():
    return _read_json(MONITOR_24H_FILE, {})
