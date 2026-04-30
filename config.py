"""
Strategy Factory Bot Manager — Configuration
All tunable settings live here. Paths respect STRATEGY_FACTORY_DATA_DIR env var
so Railway / cloud deployments can point at a persistent volume.
"""
import os

_HERE = os.path.dirname(__file__)

# === Data paths (env-overridable for cloud deploys with a volume) ===
DATA_DIR = os.environ.get("STRATEGY_FACTORY_DATA_DIR", os.path.join(_HERE, "data"))
REPORT_DIR = os.environ.get("STRATEGY_FACTORY_REPORT_DIR", os.path.join(_HERE, "reports"))
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)

# === Exchange / Data Source ===
BINANCE_BASE_URL = "https://api.binance.com"

# === Database ===
DB_PATH = os.environ.get("STRATEGY_FACTORY_DB", os.path.join(DATA_DIR, "strategy_factory.db"))

# ═══════════════════════════════════════════════════════════════════
# Adaptive Breakout Strategy — Proven on TradingView with real data
# BTC +99%, ETH +142%, SOL +121%, TSLA +113% on 4h timeframe
# ═══════════════════════════════════════════════════════════════════

# === Asset Universe ===
CRYPTO_ASSETS = ["BTC", "ETH", "SOL", "XRP", "LINK", "AVAX", "ADA", "UNI", "AAVE", "LTC"]
STOCK_ASSETS = ["TSLA", "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META"]

# === Strategy Parameters (match TradingView Pine Script exactly) ===
STRATEGY_NAME = "adaptive_breakout"
STRATEGY_TIMEFRAME = "4h"
DONCHIAN_PERIOD = 20          # Donchian channel lookback
ADX_PERIOD = 14               # ADX smoothing period
ADX_ENTRY_THRESHOLD = 20      # Only enter when ADX > 20 (trending)
ADX_EXIT_THRESHOLD = 15       # Exit when ADX drops below 15 (trend dying)
ATR_TRAIL_MULTIPLIER = 3.0    # Trailing stop = 3x ATR(14) from peak
HARD_STOP_PCT = 8.0           # Hard stop loss = 8% from entry
CRYPTO_MIN_ATR_PCT = 0.5      # Min volatility for crypto entries
STOCK_MIN_ATR_PCT = 0.3       # Min volatility for stock entries

# === Concurrency Limits ===
MAX_CONCURRENT_CRYPTO = 3     # Max 3 crypto positions at once
MAX_CONCURRENT_STOCKS = 3     # Max 3 stock positions at once

# === Cooldown ===
POST_LOSS_COOLDOWN_BARS = 2   # 2 bars = 8 hours cooldown after a loss

# === Check Intervals ===
CRYPTO_CHECK_INTERVAL_HOURS = 4   # Crypto: check every 4h (24/7)
STOCK_CHECK_INTERVAL_HOURS = 4    # Stocks: check every 4h (market hours only)

# === Pause Thresholds ===
PAUSE_WIN_RATE = 45.0
PAUSE_MAX_DRAWDOWN = -20.0
PAUSE_PROFIT_FACTOR = 1.05
PAUSE_CONSECUTIVE_LOSSES = 6
PAUSE_SHARPE_RATIO = 0.3
PAUSE_AVG_LOSS_TO_WIN = 2.0
MIN_TOTAL_TRADES = 10
MIN_WIN_RATE = 45.0

# === Reactivation Thresholds ===
REACTIVATE_WIN_RATE = 52.0
REACTIVATE_PROFIT_FACTOR = 1.2
REACTIVATE_SHARPE = 0.6
REACTIVATE_MIN_TRADES = 20

# === Learning Engine ===
LEARNING_STATE_FILE = os.path.join(DATA_DIR, "learning_state.json")
LOOKBACK_TRADES = 20
REGIME_LOOKBACK = 20

# === Reports & Logging ===
LOG_FILE = os.path.join(DATA_DIR, "bot_manager.log")
VERBOSE = True

# === Dashboard ===
DASHBOARD_OUTPUT = os.path.join(REPORT_DIR, "dashboard.html")

# === Scheduling ===
SCHEDULE_HOUR = 10
SCHEDULE_TIMEZONE = "US/Eastern"
