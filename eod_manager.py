"""
End-of-Day (EOD) Position Manager

Rules:
  1. At 3:45 PM ET every trading day (including Fridays), sell any
     position that is still in profit AFTER estimated round-trip fees.
     This locks in gains before after-hours volatility can erode them.
     The 3:45 start gives a 15-minute window so the auto-trader cycle
     (every 15 min) is guaranteed to catch it before the 4:00 PM close.

  2. Positions that are net-negative — true losers AND positions that
     are green on paper but would turn red once fees are deducted — are
     kept overnight rather than booking a loss right before close.  They
     get another chance next morning.

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

# ── Fee model ─────────────────────────────────────────────────────
# Estimated cost per side, as a percent of notional (1 bp = 0.01%).
# Mirrors conservative_mode.EST_FEE_PCT_STOCK so EOD net-of-fee math
# uses the same assumption as the rest of the system.  A round trip
# pays this on BOTH the entry notional and the exit notional.
try:
    from conservative_mode import EST_FEE_PCT_STOCK as _EST_FEE_PCT_STOCK
except Exception:
    _EST_FEE_PCT_STOCK = 0.01


def _est_round_trip_fee(entry_notional, exit_notional):
    """Estimated round-trip fee in dollars (entry side + exit side)."""
    rate = _EST_FEE_PCT_STOCK / 100.0
    return (abs(entry_notional) + abs(exit_notional)) * rate

# ── Configuration ─────────────────────────────────────────────────
# Time to start EOD closes (ET).  3:45 PM gives a 15-minute window
# so that even with the auto-trader's 15-min cycle interval, at
# least one cycle is guaranteed to land before the 4:00 PM close.
# (e.g. if last cycle was 3:44, next at 3:59 still catches it.)
EOD_HOUR = int(os.environ.get("EOD_CLOSE_HOUR", "15"))
EOD_MINUTE = int(os.environ.get("EOD_CLOSE_MINUTE", "45"))


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

    # Check if we're in the EOD window (3:50 PM ET to 3:59 PM ET)
    # Orders MUST be submitted before 4:00 PM close to fill same-day.
    current_minutes = now_et.hour * 60 + now_et.minute
    eod_minutes = EOD_HOUR * 60 + EOD_MINUTE

    if current_minutes < eod_minutes:
        return None  # Not time yet

    # Hard cutoff at 3:59 PM — after this the market closes in <1 min
    # and orders risk not filling.  Previous 4:05 cutoff caused orders
    # to be submitted after close, sitting as "accepted" but unfilled.
    if current_minutes >= (16 * 60):
        return None

    # Check if already ran today
    state = _read_state()
    if state.get("last_eod_date") == today:
        return None  # Already done today

    logger.info(f"EOD profit-taking triggered at {now_et.strftime('%I:%M %p ET')}")

    try:
        from alpaca_client import AlpacaPaperClient, is_us_market_open

        # Double-check: don't submit orders if market already closed
        if not is_us_market_open():
            logger.warning("EOD: Market is closed — skipping (orders would not fill)")
            return None

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

            # Net-of-fees green check.  We only lock in a position at EOD if
            # it is STILL profitable after paying the estimated round-trip
            # fee (entry side + exit side).  A position that is green on
            # paper but would go red once fees are deducted is treated as a
            # loss and kept overnight, same as any other red position.
            #   entry_notional ≈ cost_basis = market_value - unrealized_pl
            #   exit_notional  ≈ market_value (what we'd sell for now)
            entry_notional = market_value - unrealized_pl
            est_fee = _est_round_trip_fee(entry_notional, market_value)
            net_after_fees = unrealized_pl - est_fee

            # Only sell positions that stay green AFTER fees — keep the rest
            # (true losers + marginal "green on paper, red after fees") for
            # next morning.
            if net_after_fees > 0:
                close_reason = (
                    f"EOD profit-taking: locking in ${net_after_fees:+.2f} net "
                    f"(gross ${unrealized_pl:+.2f}, est fee ${est_fee:.2f}, "
                    f"{unrealized_plpc:+.2f}%)"
                )
                try:
                    # Submit AND confirm the fill before booking the close.  A
                    # submitted EOD order can expire/cancel without filling near
                    # the bell; booking P&L from the pre-trade mark would corrupt
                    # the journal and the conservative-mode daily ladder.  Only
                    # book once Alpaca reports status == 'filled', recomputing the
                    # realized net from the actual fill price.
                    order_result = client.close_position_confirmed(symbol)
                    fill_status = (order_result.get("status") or "").lower()
                    if order_result.get("error") or fill_status != "filled":
                        results.append({
                            "symbol": symbol,
                            "action": "close_unconfirmed",
                            "reason": (f"Close order not filled (status "
                                       f"'{fill_status or order_result.get('error') or 'unknown'}') "
                                       f"— kept for next morning"),
                            "unrealized_pl": round(unrealized_pl, 2),
                            "order": order_result,
                        })
                        logger.warning(f"  EOD {symbol}: close not filled, keeping position open")
                        continue

                    # Recompute realized net from the confirmed fill price.
                    fill_px = float(order_result.get("filled_avg_price") or 0)
                    fill_qty = float(order_result.get("filled_qty") or 0)
                    if fill_px > 0 and fill_qty > 0:
                        exit_notional = fill_px * fill_qty
                        # entry_notional was computed above (cost basis estimate)
                        realized_gross = exit_notional - entry_notional
                        realized_fee = _est_round_trip_fee(entry_notional, exit_notional)
                        net_after_fees = realized_gross - realized_fee
                        est_fee = realized_fee
                        market_value = exit_notional
                        unrealized_pl = realized_gross

                    results.append({
                        "symbol": symbol,
                        "action": "closed",
                        "reason": close_reason,
                        "unrealized_pl": round(unrealized_pl, 2),
                        "net_pl_after_fees": round(net_after_fees, 2),
                        "est_fee": round(est_fee, 2),
                        "unrealized_plpc": round(unrealized_plpc, 2),
                        "market_value": round(market_value, 2),
                        "order": order_result,
                    })
                    total_pl += net_after_fees
                    logger.info(f"  EOD closed {symbol}: {close_reason}")

                    # Record in conservative mode (net of estimated fees so
                    # the daily ladder reflects true realized P&L).
                    try:
                        from conservative_mode import ConservativeMode
                        cm = ConservativeMode()
                        cm.record_trade_result(
                            symbol=symbol,
                            strategy="eod_close",
                            net_pl=net_after_fees,
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
                # Net loss (true red, or green-on-paper but red after fees) —
                # keep for next morning rather than booking a loss now.
                if unrealized_pl > 0:
                    keep_reason = (
                        f"Green on paper (${unrealized_pl:+.2f}) but net "
                        f"${net_after_fees:+.2f} after est fee ${est_fee:.2f} — "
                        f"keeping for next morning"
                    )
                else:
                    keep_reason = (
                        f"In loss (${unrealized_pl:+.2f}), keeping for next morning"
                    )
                results.append({
                    "symbol": symbol,
                    "action": "kept",
                    "reason": keep_reason,
                    "unrealized_pl": round(unrealized_pl, 2),
                    "net_pl_after_fees": round(net_after_fees, 2),
                    "est_fee": round(est_fee, 2),
                })
                logger.info(f"  EOD keeping {symbol}: {keep_reason}")

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
        "rule": "Sell positions still green AFTER fees at 3:45 PM ET; keep net-negative positions for next morning",
    }
