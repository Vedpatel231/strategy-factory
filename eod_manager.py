"""
End-of-Day (EOD) Position Manager

Rules:
  1. At 3:55 PM ET every trading day (including Fridays), sell any
     positions that are in profit.  This locks in gains before
     after-hours volatility can erode them.

  2. Positions in the red are kept overnight — no point selling at a
     loss right before close.  They get another chance next morning.

  3. Sends Telegram notifications for every EOD close.

  4. Tracks state so EOD closes only run once per day.

The auto-trader loop calls ``check_eod()`` every cycle.  If the time
window hasn't been reached yet, or the EOD close already ran today,
it returns immediately.
"""

import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger("eod_manager")

STATE_FILE = os.path.join(config.DATA_DIR, "eod_manager_state.json")

# ── Configuration ─────────────────────────────────────────────────
# Time to start EOD closes (ET).  3:55 PM gives 5 minutes for orders
# to fill before the 4:00 PM close.
EOD_HOUR = int(os.environ.get("EOD_CLOSE_HOUR", "15"))
EOD_MINUTE = int(os.environ.get("EOD_CLOSE_MINUTE", "55"))


def _et_now():
    """Current time in US Eastern."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))


def _today_str():
    return _et_now().strftime("%Y-%m-%d")


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


def _send_telegram(message, level="info"):
    """Send a Telegram alert if configured."""
    try:
        from telegram_notifier import send_alert, is_configured
        if is_configured():
            send_alert(message, level=level)
    except Exception as e:
        logger.warning(f"Telegram notification failed: {e}")


def check_eod():
    """Main entry point — called every auto-trader cycle.

    Returns a dict with results if EOD closes were triggered,
    or None if it's not time yet / already done today.
    """
    now_et = _et_now()
    today = now_et.strftime("%Y-%m-%d")
    weekday = now_et.weekday()  # Mon=0, Fri=4, Sat=5, Sun=6

    # Only run on weekdays
    if weekday >= 5:
        return None

    # Check if we're in the EOD window (3:55 PM ET or later, before midnight)
    current_minutes = now_et.hour * 60 + now_et.minute
    eod_minutes = EOD_HOUR * 60 + EOD_MINUTE

    if current_minutes < eod_minutes:
        return None  # Not time yet

    # Don't run after 4:05 PM — market is closed, orders won't fill
    if current_minutes > (16 * 60 + 5):
        return None

    # Check if already ran today
    state = _read_state()
    if state.get("last_eod_date") == today:
        return None  # Already done today

    logger.info(f"EOD profit-taking triggered at {now_et.strftime('%I:%M %p ET')}")

    try:
        from alpaca_client import AlpacaPaperClient
        client = AlpacaPaperClient()
        positions = client.get_positions(live_prices=False)

        if not positions:
            logger.info("EOD: No open positions — nothing to close")
            state["last_eod_date"] = today
            state["last_eod_result"] = "no_positions"
            _write_state(state)
            return {"action": "no_positions", "date": today}

        results = []
        total_pl = 0.0

        for pos in positions:
            symbol = pos.get("symbol", "?")
            unrealized_pl = float(pos.get("unrealized_pl", 0))
            unrealized_plpc = float(pos.get("unrealized_plpc", 0))
            market_value = float(pos.get("market_value", 0))
            current_price = float(pos.get("current_price", 0))
            avg_entry = float(pos.get("avg_entry_price", 0))

            # Only sell positions in profit — keep red ones for next morning
            if unrealized_pl > 0:
                close_reason = f"EOD profit-taking: locking in ${unrealized_pl:+.2f} ({unrealized_plpc:+.2f}%)"
                try:
                    order_result = client.close_position(symbol)
                    results.append({
                        "symbol": symbol,
                        "action": "closed",
                        "reason": close_reason,
                        "unrealized_pl": round(unrealized_pl, 2),
                        "unrealized_plpc": round(unrealized_plpc, 2),
                        "market_value": round(market_value, 2),
                        "order": order_result,
                    })
                    total_pl += unrealized_pl
                    logger.info(f"  EOD closed {symbol}: {close_reason}")

                    # Record in conservative mode
                    try:
                        from conservative_mode import ConservativeMode
                        cm = ConservativeMode()
                        cm.record_trade_result(
                            symbol=symbol,
                            strategy="eod_close",
                            net_pl=unrealized_pl,
                            reason=close_reason,
                        )
                        cm.release_open_risk(symbol)
                    except Exception as e:
                        logger.warning(f"Could not record EOD close in conservative mode: {e}")

                    # Clean up risk book
                    try:
                        from trade_journal import PositionRiskBook
                        PositionRiskBook().remove(symbol)
                    except Exception:
                        pass

                except Exception as e:
                    logger.error(f"  EOD failed to close {symbol}: {e}")
                    results.append({
                        "symbol": symbol,
                        "action": "error",
                        "error": str(e),
                        "unrealized_pl": round(unrealized_pl, 2),
                    })
            else:
                # In loss — keep for next morning, no point selling red
                results.append({
                    "symbol": symbol,
                    "action": "kept",
                    "reason": f"In loss (${unrealized_pl:+.2f}), keeping for next morning",
                    "unrealized_pl": round(unrealized_pl, 2),
                })
                logger.info(f"  EOD keeping {symbol}: in loss ${unrealized_pl:+.2f}")

        # Save state
        state["last_eod_date"] = today
        state["last_eod_result"] = {
            "timestamp": _et_now().isoformat(),
            "positions_checked": len(positions),
            "positions_closed": sum(1 for r in results if r["action"] == "closed"),
            "positions_kept": sum(1 for r in results if r["action"] == "kept"),
            "total_pl_locked": round(total_pl, 2),
            "details": results,
        }
        _write_state(state)

        # Send Telegram notification
        closed_count = sum(1 for r in results if r["action"] == "closed")
        kept_count = sum(1 for r in results if r["action"] == "kept")

        if closed_count > 0:
            closed_symbols = [r["symbol"] for r in results if r["action"] == "closed"]
            msg_lines = []
            msg_lines.append("🔔 END-OF-DAY PROFIT LOCK")
            msg_lines.append(f"Closed {closed_count} position(s): {', '.join(closed_symbols)}")
            msg_lines.append(f"Total P&L locked: ${total_pl:+.2f}")
            if kept_count > 0:
                kept_symbols = [r["symbol"] for r in results if r["action"] == "kept"]
                msg_lines.append(f"Kept {kept_count} (in loss): {', '.join(kept_symbols)}")
            _send_telegram("\n".join(msg_lines), level="info")

        logger.info(f"EOD complete: closed={closed_count}, kept={kept_count}, "
                     f"P&L locked=${total_pl:+.2f}")
        return state["last_eod_result"]

    except Exception as e:
        logger.error(f"EOD manager error: {e}", exc_info=True)
        state["last_eod_date"] = today
        state["last_eod_result"] = {"error": str(e)}
        _write_state(state)
        return {"error": str(e)}


def get_status():
    """Return EOD manager status for dashboard/API."""
    state = _read_state()
    now_et = _et_now()
    today = now_et.strftime("%Y-%m-%d")
    weekday = now_et.weekday()

    current_minutes = now_et.hour * 60 + now_et.minute
    eod_minutes = EOD_HOUR * 60 + EOD_MINUTE

    return {
        "eod_time_et": f"{EOD_HOUR}:{EOD_MINUTE:02d}",
        "current_time_et": now_et.strftime("%H:%M"),
        "minutes_until_eod": max(0, eod_minutes - current_minutes) if weekday < 5 else None,
        "eod_ran_today": state.get("last_eod_date") == today,
        "last_eod_date": state.get("last_eod_date"),
        "last_eod_result": state.get("last_eod_result"),
        "rule": "Sell profitable (green) positions at 3:55 PM ET; keep red positions for next morning",
    }
