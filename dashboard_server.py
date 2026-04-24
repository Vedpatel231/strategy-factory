"""
Strategy Factory — Dashboard Server

Serves the static dashboard HTML AND exposes local paper-trading actions
as JSON endpoints that the dashboard UI calls via fetch().

Launch:
    python dashboard_server.py

Then open http://127.0.0.1:8765 in your browser.

Endpoints:
    GET  /                      → serves dashboard.html
    GET  /api/status            → health + config
    GET  /api/last-refresh      → timestamp of last daily_runner run
    GET  /api/broker/connect    → initialise broker, return account info
    GET  /api/broker/account    → account snapshot
    GET  /api/broker/positions  → open positions with simulated P&L
    GET  /api/broker/orders     → recent orders
    GET  /api/broker/preview    → dry-run preview of orders to place
    POST /api/broker/execute    → places paper orders, returns results
    POST /api/broker/close-all  → closes every open position
    POST /api/broker/reset      → resets the simulator to $1000
    POST /api/daily-run         → refresh portfolio + regenerate dashboard
"""

import os
import sys
import json
import base64
import secrets
import traceback
import subprocess
import logging
from datetime import datetime
from functools import wraps

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env first
import env_loader  # noqa: F401

try:
    from flask import Flask, jsonify, request, send_from_directory, Response
except ImportError:
    print("❌ Flask not installed. Run: pip install flask")
    sys.exit(1)

import config
from auto_trader import AutoTrader
from alpaca_auto_trader import AlpacaAutoTrader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dashboard_server")

app = Flask(__name__)

DASHBOARD_PATH = config.DASHBOARD_OUTPUT
REPORT_DIR = config.REPORT_DIR
DATA_DIR = os.environ.get("STRATEGY_FACTORY_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
LAST_REFRESH_FILE = os.path.join(DATA_DIR, "last_refresh.json")
# Railway / cloud platforms set PORT
PORT = int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8765")))
# Railway/cloud → bind to 0.0.0.0 and require a password. Local → 127.0.0.1.
IS_DEPLOYED = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RENDER") or os.getenv("FLY_APP_NAME"))
HOST = "0.0.0.0" if IS_DEPLOYED else "127.0.0.1"

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")

# Safety warning: prefer auth in deployed environments, but do not hard-fail
# the service or Railway healthcheck if the env var is temporarily missing.
if IS_DEPLOYED and not DASHBOARD_PASSWORD:
    logger.warning("DASHBOARD_PASSWORD is not set in a deployed environment; "
                   "dashboard auth is disabled until the variable is configured.")


def require_auth(func):
    """Decorator: HTTP Basic Auth when DASHBOARD_PASSWORD is set."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not DASHBOARD_PASSWORD:
            return func(*args, **kwargs)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return Response("Authentication required", 401,
                          {"WWW-Authenticate": 'Basic realm="Strategy Factory"'})
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            u, _, p = decoded.partition(":")
        except Exception:
            return Response("Invalid auth header", 401)
        if not (secrets.compare_digest(u, DASHBOARD_USERNAME)
                and secrets.compare_digest(p, DASHBOARD_PASSWORD)):
            return Response("Invalid credentials", 401,
                          {"WWW-Authenticate": 'Basic realm="Strategy Factory"'})
        return func(*args, **kwargs)
    return wrapper

# Lazy-init singleton
_paper_trader = None


def get_paper_trader():
    global _paper_trader
    if _paper_trader is not None:
        return _paper_trader, None
    try:
        from paper_trader import PaperTrader
        _paper_trader = PaperTrader(starting_balance=1000.0)
        return _paper_trader, None
    except Exception as e:
        logger.error(f"PaperTrader init failed: {e}\n{traceback.format_exc()}")
        return None, f"PaperTrader init failed: {e}"


def load_portfolio():
    p = os.path.join(REPORT_DIR, "latest_portfolio.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


# ── STATIC ROUTES ───────────────────────────────────────────────────────
@app.route("/")
@require_auth
def root():
    if not os.path.exists(DASHBOARD_PATH):
        return ("<h1>Dashboard not found</h1>"
                "<p>Run <code>python daily_runner.py</code> first to generate it, "
                "then refresh this page.</p>"), 404
    return send_from_directory(REPORT_DIR, "dashboard.html")


@app.route("/<path:filename>")
@require_auth
def static_files(filename):
    return send_from_directory(REPORT_DIR, filename)


# ── STATUS (public — used by Railway healthcheck, no auth required) ────
@app.route("/api/status")
def status():
    portfolio = load_portfolio()
    return jsonify({
        "ok": True,
        "server_time": datetime.utcnow().isoformat(),
        "simulator": "local",
        "starting_balance": 1000.0,
        "portfolio_loaded": portfolio is not None,
        "portfolio_strategies": portfolio["summary"]["num_strategies"] if portfolio else 0,
        "expected_monthly_return_pct": portfolio["summary"].get("expected_monthly_return_pct", 0) if portfolio else 0,
        "dashboard_path": DASHBOARD_PATH,
        "auth_enabled": bool(DASHBOARD_PASSWORD),
        "deployed": IS_DEPLOYED,
    })


# ── LAST REFRESH ────────────────────────────────────────────────────────
@app.route("/api/last-refresh")
@require_auth
def last_refresh():
    if not os.path.exists(LAST_REFRESH_FILE):
        return jsonify({"refreshed": False, "message": "No refresh has been recorded yet"})
    try:
        with open(LAST_REFRESH_FILE) as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"refreshed": False, "error": str(e)})


# ── BROKER ENDPOINTS ────────────────────────────────────────────────────
@app.route("/api/broker/connect")
@require_auth
def broker_connect():
    trader, err = get_paper_trader()
    if err:
        return jsonify({"connected": False, "error": err}), 500
    try:
        return jsonify({"connected": True, "account": trader.client.get_account()})
    except Exception as e:
        logger.error(f"Connect failed: {e}\n{traceback.format_exc()}")
        return jsonify({"connected": False, "error": str(e)}), 500


@app.route("/api/broker/account")
@require_auth
def broker_account():
    trader, err = get_paper_trader()
    if err:
        return jsonify({"error": err}), 500
    return jsonify(trader.client.get_account())


@app.route("/api/broker/positions")
@require_auth
def broker_positions():
    trader, err = get_paper_trader()
    if err:
        return jsonify({"error": err}), 500
    try:
        positions = trader.client.get_positions()
        total_pl = sum(p["unrealized_pl"] for p in positions)
        total_value = sum(p["market_value"] for p in positions)
        total_cost = sum(p["cost_basis"] for p in positions)
        return jsonify({
            "positions": positions,
            "summary": {
                "count": len(positions),
                "total_market_value": round(total_value, 2),
                "total_cost_basis": round(total_cost, 2),
                "total_unrealized_pl": round(total_pl, 2),
                "total_unrealized_plpc": round(total_pl / total_cost * 100, 2) if total_cost > 0 else 0,
            }
        })
    except Exception as e:
        logger.error(f"Positions failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/broker/orders")
@require_auth
def broker_orders():
    trader, err = get_paper_trader()
    if err:
        return jsonify({"error": err}), 500
    try:
        limit = int(request.args.get("limit", 25))
        status = request.args.get("status", "all")
        return jsonify({"orders": trader.client.get_orders(limit=limit, status=status)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/broker/preview")
@require_auth
def broker_preview():
    trader, err = get_paper_trader()
    if err:
        return jsonify({"error": err}), 500
    portfolio = load_portfolio()
    if not portfolio:
        return jsonify({"error": "No portfolio found. Run a daily analysis first."}), 400
    try:
        return jsonify(trader.execute_portfolio(portfolio, dry_run=True))
    except Exception as e:
        logger.error(f"Preview failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/broker/execute", methods=["POST"])
@require_auth
def broker_execute():
    trader, err = get_paper_trader()
    if err:
        return jsonify({"error": err}), 500
    portfolio = load_portfolio()
    if not portfolio:
        return jsonify({"error": "No portfolio found. Run a daily analysis first."}), 400
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true' in request body"}), 400
    try:
        results = trader.execute_portfolio(portfolio, dry_run=False)
        logger.info(f"Executed: {results.get('summary', {})}")
        return jsonify(results)
    except Exception as e:
        logger.error(f"Execute failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/broker/close-all", methods=["POST"])
@require_auth
def broker_close_all():
    trader, err = get_paper_trader()
    if err:
        return jsonify({"error": err}), 500
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true' in request body"}), 400
    try:
        return jsonify({"closed": trader.client.close_all_positions()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/broker/reset", methods=["POST"])
@require_auth
def broker_reset():
    trader, err = get_paper_trader()
    if err:
        return jsonify({"error": err}), 500
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true' in request body"}), 400
    try:
        balance = float(data.get("starting_balance", 1000.0))
        acct = trader.client.reset_account(starting_balance=balance)
        return jsonify({"reset": True, "account": acct})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── DAILY P&L CALENDAR DATA ────────────────────────────────────────────
@app.route("/api/broker/daily-pnl")
@require_auth
def broker_daily_pnl():
    """Return all daily P&L snapshots for the calendar view."""
    from paper_broker import get_daily_pnl
    data = get_daily_pnl()
    # Also record today's snapshot if broker is available
    trader, err = get_paper_trader()
    if not err:
        try:
            today_snap = trader.client.record_daily_snapshot()
            data[today_snap["date"]] = today_snap
        except Exception:
            pass
    return jsonify({"snapshots": data})


# ── ALPACA PAPER TRADING ENDPOINTS ─────────────────────────────────────
_alpaca_client = None


def get_alpaca_client():
    global _alpaca_client
    if _alpaca_client is not None:
        return _alpaca_client, None
    try:
        from alpaca_client import AlpacaPaperClient, is_configured
        if not is_configured():
            return None, "Alpaca API keys not configured. Set ALPACA_API_KEY and ALPACA_API_SECRET."
        _alpaca_client = AlpacaPaperClient()
        return _alpaca_client, None
    except Exception as e:
        logger.error(f"Alpaca client init failed: {e}\n{traceback.format_exc()}")
        return None, f"Alpaca init failed: {e}"


@app.route("/api/alpaca/status")
@require_auth
def alpaca_status():
    """Check if Alpaca keys are configured (doesn't connect yet)."""
    try:
        from alpaca_client import is_configured
        return jsonify({
            "configured": is_configured(),
            "broker": "alpaca_paper",
        })
    except Exception as e:
        return jsonify({"configured": False, "error": str(e)})


@app.route("/api/alpaca/connect", methods=["POST"])
@require_auth
def alpaca_connect():
    client, err = get_alpaca_client()
    if err:
        return jsonify({"connected": False, "error": err}), 500
    try:
        acct = client.connect()
        return jsonify({"connected": True, "account": acct})
    except Exception as e:
        logger.error(f"Alpaca connect failed: {e}\n{traceback.format_exc()}")
        return jsonify({"connected": False, "error": str(e)}), 500


@app.route("/api/alpaca/account")
@require_auth
def alpaca_account():
    client, err = get_alpaca_client()
    if err:
        return jsonify({"error": err}), 500
    try:
        return jsonify(client.get_account())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alpaca/positions")
@require_auth
def alpaca_positions():
    client, err = get_alpaca_client()
    if err:
        return jsonify({"error": err}), 500
    try:
        live_prices = request.args.get("live", "0").lower() in ("1", "true", "yes")
        positions = client.get_positions(live_prices=live_prices)
        total_pl = sum(p["unrealized_pl"] for p in positions)
        total_value = sum(p["market_value"] for p in positions)
        total_cost = sum(p["cost_basis"] for p in positions)
        return jsonify({
            "positions": positions,
            "live_prices": live_prices,
            "summary": {
                "count": len(positions),
                "total_market_value": round(total_value, 2),
                "total_cost_basis": round(total_cost, 2),
                "total_unrealized_pl": round(total_pl, 2),
                "total_unrealized_plpc": round(total_pl / total_cost * 100, 2) if total_cost > 0 else 0,
            }
        })
    except Exception as e:
        logger.error(f"Alpaca positions failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/alpaca/orders")
@require_auth
def alpaca_orders():
    client, err = get_alpaca_client()
    if err:
        return jsonify({"error": err}), 500
    try:
        limit = int(request.args.get("limit", 25))
        status = request.args.get("status", "all")
        return jsonify({"orders": client.get_orders(limit=limit, status=status)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alpaca/daily-pnl")
@require_auth
def alpaca_daily_pnl():
    """Return Alpaca daily equity history for the overview calendar."""
    client, err = get_alpaca_client()
    if err:
        return jsonify({"error": err}), 500
    try:
        period = request.args.get("period", "1A")
        return jsonify({"snapshots": client.get_daily_pnl(period=period)})
    except Exception as e:
        logger.error(f"Alpaca daily P&L failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/alpaca/execute", methods=["POST"])
@require_auth
def alpaca_execute():
    """Execute a single order on Alpaca paper trading."""
    client, err = get_alpaca_client()
    if err:
        return jsonify({"error": err}), 500
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true' in request body"}), 400
    symbol = data.get("symbol")
    notional = data.get("notional")
    side = data.get("side", "buy")
    if not symbol or not notional:
        return jsonify({"error": "Missing 'symbol' and/or 'notional'"}), 400
    try:
        result = client.submit_order(symbol, float(notional), side=side)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Alpaca execute failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/alpaca/close-position", methods=["POST"])
@require_auth
def alpaca_close_position():
    client, err = get_alpaca_client()
    if err:
        return jsonify({"error": err}), 500
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true'"}), 400
    symbol = data.get("symbol")
    if not symbol:
        return jsonify({"error": "Missing 'symbol'"}), 400
    try:
        from alpaca_client import normalize_crypto_symbol
        from trade_journal import PositionRiskBook, TradeJournal
        sym = normalize_crypto_symbol(symbol)
        position = client.get_position(sym) or {}
        risk_book = PositionRiskBook()
        entry_state = risk_book.get(sym)
        entry_price = float((entry_state or {}).get("entry_price") or position.get("avg_entry_price") or 0)
        current_price = float(position.get("current_price") or 0)
        pl_pct = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 and current_price > 0 else 0.0
        result = client.close_position(sym)
        if not result.get("error"):
            risk_book.remove(sym)
            TradeJournal().append({
                "event": "position_closed",
                "symbol": sym,
                "side": "close",
                "reason": "Manual dashboard close",
                "entry_state": entry_state,
                "exit_price": current_price,
                "exit_notional": position.get("market_value"),
                "unrealized_pl_pct": round(pl_pct, 2),
                "order": result,
            })
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alpaca/close-all", methods=["POST"])
@require_auth
def alpaca_close_all():
    client, err = get_alpaca_client()
    if err:
        return jsonify({"error": err}), 500
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true'"}), 400
    try:
        from trade_journal import PositionRiskBook, TradeJournal
        risk_book = PositionRiskBook()
        journal = TradeJournal()
        closed = []
        for position in client.get_positions(live_prices=True):
            sym = position.get("symbol")
            entry_state = risk_book.get(sym)
            entry_price = float((entry_state or {}).get("entry_price") or position.get("avg_entry_price") or 0)
            current_price = float(position.get("current_price") or 0)
            pl_pct = ((current_price - entry_price) / entry_price * 100.0) if entry_price > 0 and current_price > 0 else 0.0
            result = client.close_position(sym)
            closed.append(result)
            if not result.get("error"):
                risk_book.remove(sym)
                journal.append({
                    "event": "position_closed",
                    "symbol": sym,
                    "side": "close",
                    "reason": "Manual dashboard close all",
                    "entry_state": entry_state,
                    "exit_price": current_price,
                    "exit_notional": position.get("market_value"),
                    "unrealized_pl_pct": round(pl_pct, 2),
                    "order": result,
                })
        return jsonify({"closed": closed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/alpaca/price")
@require_auth
def alpaca_price():
    client, err = get_alpaca_client()
    if err:
        return jsonify({"error": err}), 500
    symbol = request.args.get("symbol")
    if not symbol:
        return jsonify({"error": "Missing 'symbol' query param"}), 400
    try:
        price = client.get_latest_price(symbol)
        return jsonify({"symbol": symbol, "price": price})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── ALPACA AUTO-TRADER ENDPOINTS ───────────────────────────────────────
@app.route("/api/alpaca/auto/status")
@require_auth
def alpaca_auto_status():
    return jsonify(AlpacaAutoTrader.get().status())


@app.route("/api/alpaca/auto/toggle", methods=["POST"])
@require_auth
def alpaca_auto_toggle():
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"error": "Missing 'enabled' in request body"}), 400
    AlpacaAutoTrader.set_enabled(bool(data["enabled"]))
    return jsonify({"enabled": AlpacaAutoTrader.is_enabled(), "status": AlpacaAutoTrader.get().status()})


@app.route("/api/alpaca/auto/run-now", methods=["POST"])
@require_auth
def alpaca_auto_run_now():
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true'"}), 400
    return jsonify(AlpacaAutoTrader.get().trigger_now())


@app.route("/api/alpaca/auto/preview")
@require_auth
def alpaca_auto_preview():
    """Dry-run preview of what Alpaca auto-trade would do."""
    try:
        from alpaca_trader import AlpacaTrader
        from alpaca_client import is_configured
        if not is_configured():
            return jsonify({"error": "Alpaca API keys not configured"}), 400
        portfolio = load_portfolio()
        if not portfolio:
            return jsonify({"error": "No portfolio found. Run a daily analysis first."}), 400
        trader = AlpacaTrader()
        return jsonify(trader.execute_portfolio(portfolio, dry_run=True))
    except Exception as e:
        logger.error(f"Alpaca preview failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/alpaca/auto/execute", methods=["POST"])
@require_auth
def alpaca_auto_execute():
    """Execute portfolio rebalance on Alpaca (manual trigger)."""
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true'"}), 400
    try:
        from alpaca_trader import AlpacaTrader
        from alpaca_client import is_configured
        if not is_configured():
            return jsonify({"error": "Alpaca API keys not configured"}), 400
        portfolio = load_portfolio()
        if not portfolio:
            return jsonify({"error": "No portfolio found. Run a daily analysis first."}), 400
        trader = AlpacaTrader()
        results = trader.execute_portfolio(portfolio, dry_run=False)
        logger.info(f"Alpaca execute: {results.get('summary', {})}")
        return jsonify(results)
    except Exception as e:
        logger.error(f"Alpaca execute failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


# ── EMERGENCY KILL SWITCH & RISK ──────────────────────────────────────
@app.route("/api/emergency/kill", methods=["POST"])
@require_auth
def emergency_kill():
    """Emergency: close all positions, disable all auto-trading."""
    results = {"timestamp": datetime.utcnow().isoformat(), "actions": []}

    # Disable auto-trading
    try:
        AlpacaAutoTrader.set_enabled(False)
        trader = AlpacaAutoTrader.get()
        trader.stop()
        results["actions"].append("Alpaca auto-trading disabled")
    except Exception as e:
        results["actions"].append(f"Error disabling auto-trade: {e}")

    # Close all Alpaca positions
    try:
        from alpaca_client import AlpacaPaperClient
        client = AlpacaPaperClient()
        positions = client.get_positions()
        for pos in positions:
            try:
                client.close_position(pos["symbol"])
                results["actions"].append(f"Closed {pos['symbol']} (${pos.get('market_value', 0):.2f})")
            except Exception as e:
                results["actions"].append(f"Failed to close {pos['symbol']}: {e}")
        results["positions_closed"] = len(positions)
    except Exception as e:
        results["actions"].append(f"Error closing positions: {e}")

    # Log the kill event
    kill_log = os.path.join(DATA_DIR, "kill_switch.log.json")
    try:
        existing = []
        if os.path.exists(kill_log):
            with open(kill_log) as f:
                existing = json.load(f)
        existing.append(results)
        with open(kill_log, "w") as f:
            json.dump(existing[-50:], f, indent=2, default=str)
    except Exception:
        pass

    return jsonify(results)


@app.route("/api/risk/status")
@require_auth
def risk_status():
    """Return current risk control states."""
    try:
        from risk_manager import RiskManager
        rm = RiskManager()
        return jsonify(rm.get_status())
    except ImportError:
        return jsonify({"error": "risk_manager not available", "controls_active": False})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/intraday/state")
@require_auth
def intraday_state():
    """Return latest intraday signal/regime evaluations by symbol."""
    try:
        from intraday_engine import load_intraday_state
        return jsonify({
            "source": "live_intraday_signal_engine",
            "seeded_metrics_included": False,
            "symbols": load_intraday_state(),
        })
    except Exception as e:
        return jsonify({"error": str(e), "symbols": {}})


@app.route("/api/trade-journal")
@require_auth
def trade_journal():
    """Return real paper-trading decisions/fills recorded by AlpacaTrader."""
    try:
        limit = int(request.args.get("limit", 200))
        from trade_journal import load_trade_journal
        return jsonify({
            "source": "alpaca_paper_trade_journal",
            "seeded_metrics_included": False,
            "events": load_trade_journal(limit=limit),
        })
    except Exception as e:
        return jsonify({"error": str(e), "events": []})


@app.route("/api/learning/live-status")
@require_auth
def learning_live_status():
    """Return real-paper learning state and currently blocked strategy/regime pairs."""
    try:
        from learning_engine import LearningEngine

        learner = LearningEngine()
        strategies = []
        blocked_pairs = []
        for strategy_id, strategy_state in (learner.state.get("strategies") or {}).items():
            real_regimes = strategy_state.get("real_regime_performance") or {}
            total_trades = 0
            total_wins = 0
            total_pnl = 0.0
            for regime, perf in real_regimes.items():
                trades = int(perf.get("trades", 0) or 0)
                wins = int(perf.get("wins", 0) or 0)
                pnl = float(perf.get("pnl", 0) or 0)
                total_trades += trades
                total_wins += wins
                total_pnl += pnl
                blocked, reason = learner.should_block_strategy(strategy_id, regime)
                if blocked:
                    blocked_pairs.append({
                        "strategy": strategy_id,
                        "regime": regime,
                        "trades": trades,
                        "wins": wins,
                        "win_rate": float(perf.get("win_rate", 0) or 0),
                        "pnl": round(pnl, 2),
                        "reason": reason,
                    })
            strategies.append({
                "strategy": strategy_id,
                "trades": total_trades,
                "wins": total_wins,
                "win_rate": round(total_wins / total_trades * 100, 1) if total_trades else 0.0,
                "pnl": round(total_pnl, 2),
                "real_regime_performance": real_regimes,
                "real_symbol_performance": strategy_state.get("real_symbol_performance") or {},
            })

        blocked_pairs.sort(key=lambda row: (row["strategy"], row["regime"]))
        strategies.sort(key=lambda row: (row["pnl"], row["trades"]), reverse=True)
        return jsonify({
            "source": "learning_engine_real_paper_state",
            "seeded_metrics_included": False,
            "strategies": strategies,
            "blocked_pairs": blocked_pairs,
        })
    except Exception as e:
        logger.error(f"Learning live status failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "strategies": [], "blocked_pairs": []})


@app.route("/api/monitor/24h")
@require_auth
def monitor_24h():
    """Return the rolling 24h live monitor snapshot."""
    try:
        from live_monitor import load_live_monitor_snapshot, write_live_monitor_snapshot

        refresh = request.args.get("refresh", "0").lower() in ("1", "true", "yes")
        data = write_live_monitor_snapshot(hours=24) if refresh else load_live_monitor_snapshot()
        return jsonify({
            "source": "live_monitor_24h",
            "path": os.path.join(DATA_DIR, "live_monitor_24h.json"),
            "data": data,
        })
    except Exception as e:
        logger.error(f"24h monitor failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "data": {}})


@app.route("/api/position-risk")
@require_auth
def position_risk():
    """Return per-position SL/TP/trailing/timeout metadata."""
    try:
        from trade_journal import load_position_risk_state
        return jsonify({
            "source": "position_risk_book",
            "positions": load_position_risk_state(),
        })
    except Exception as e:
        return jsonify({"error": str(e), "positions": {}})


@app.route("/api/alpaca/fee-analysis")
@require_auth
def alpaca_fee_analysis():
    """Return estimated Alpaca crypto fees and net P&L views."""
    try:
        from trade_journal import (
            TRADE_LEDGER_CSV,
            load_position_risk_state,
            rebuild_trade_ledger_from_journal,
            summarize_fee_analysis,
        )
        positions = []
        live_prices = request.args.get("live", "0").lower() in ("1", "true", "yes")
        try:
            client, err = get_alpaca_client()
            if not err:
                positions = client.get_positions(live_prices=live_prices)
        except Exception:
            positions = []
        analysis = summarize_fee_analysis(
            open_positions=positions,
            risk_state=load_position_risk_state(),
        )
        try:
            rebuild_trade_ledger_from_journal()
        except Exception as ledger_error:
            logger.warning(f"Trade ledger rebuild failed: {ledger_error}")
        analysis["trade_ledger_csv"] = TRADE_LEDGER_CSV
        return jsonify(analysis)
    except Exception as e:
        logger.error(f"Alpaca fee analysis failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "summary": {}, "closed_trades": [], "open_trades": []})


@app.route("/api/alpaca/trade-ledger")
@require_auth
def alpaca_trade_ledger():
    """Return the persistent fee-aware trade ledger used for audits."""
    try:
        from trade_journal import TRADE_LEDGER_CSV, load_trade_ledger, rebuild_trade_ledger_from_journal
        rebuild_trade_ledger_from_journal()
        limit = int(request.args.get("limit", 500))
        rows = load_trade_ledger(limit=limit)
        net_values = []
        for row in rows:
            try:
                net_values.append(float(row.get("net_pl") or 0))
            except (TypeError, ValueError):
                net_values.append(0.0)
        wins = sum(1 for value in net_values if value > 0)
        losses = sum(1 for value in net_values if value < 0)
        return jsonify({
            "path": TRADE_LEDGER_CSV,
            "rows": rows,
            "summary": {
                "trades": len(rows),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / len(rows) * 100, 1) if rows else None,
                "net_pl": round(sum(net_values), 2),
            },
        })
    except Exception as e:
        logger.error(f"Alpaca trade ledger failed: {e}\n{traceback.format_exc()}")
        return jsonify({"error": str(e), "path": "", "rows": [], "summary": {}})


# ── AUTO-TRADER ENDPOINTS ──────────────────────────────────────────────
@app.route("/api/auto/status")
@require_auth
def auto_status():
    return jsonify(AutoTrader.get().status())


@app.route("/api/auto/toggle", methods=["POST"])
@require_auth
def auto_toggle():
    data = request.get_json(silent=True) or {}
    if "enabled" not in data:
        return jsonify({"error": "Missing 'enabled' in request body"}), 400
    AutoTrader.set_enabled(bool(data["enabled"]))
    return jsonify({"enabled": AutoTrader.is_enabled(), "status": AutoTrader.get().status()})


@app.route("/api/auto/run-now", methods=["POST"])
@require_auth
def auto_run_now():
    data = request.get_json(silent=True) or {}
    if not data.get("confirm"):
        return jsonify({"error": "Missing 'confirm: true'"}), 400
    return jsonify(AutoTrader.get().trigger_now())


# ── DAILY RUN ──────────────────────────────────────────────────────────
@app.route("/api/daily-run", methods=["POST"])
@require_auth
def daily_run():
    try:
        result = subprocess.run(
            [sys.executable, "daily_runner.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=120,
        )
        portfolio = load_portfolio()
        return jsonify({
            "ok": result.returncode == 0,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-500:] if result.stderr else "",
            "portfolio_summary": portfolio["summary"] if portfolio else None,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Daily run timed out after 120s"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def banner():
    print("\n" + "=" * 64)
    env = "☁️  DEPLOYED (auth required)" if IS_DEPLOYED else "🏠 LOCAL"
    print(f"  📊 Strategy Factory — Dashboard Server — {env}")
    print("=" * 64)
    print(f"  Bind:         http://{HOST}:{PORT}/")
    print(f"  Simulator:    local paper broker, $1,000 starting balance")
    print(f"  Price model:  synthetic math-based paper simulation")
    if DASHBOARD_PASSWORD:
        print(f"  🔒 Auth:      ON (user='{DASHBOARD_USERNAME}')")
    else:
        print(f"  🔓 Auth:      OFF (set DASHBOARD_PASSWORD env var to enable)")
    at = AutoTrader.get()
    at_on = AutoTrader.is_enabled()
    print(f"  🤖 Auto-trade: {'ON' if at_on else 'OFF'} (interval {at.interval_min}min)")
    aat = AlpacaAutoTrader.get()
    aat_on = AlpacaAutoTrader.is_enabled()
    print(f"  🦙 Alpaca auto: {'ON' if aat_on else 'OFF'} (interval {aat.interval_min}min)")
    print("=" * 64)
    print()


# Start the auto-trader thread as soon as this module loads so it also runs
# under WSGI/gunicorn on Railway (not only when __main__).
AutoTrader.get().start()
AlpacaAutoTrader.get().start()


if __name__ == "__main__":
    banner()
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
