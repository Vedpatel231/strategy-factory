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

# ═══════════════════════════════════════════════════════════════════
# Intraday Stock Strategies — RSI, MACD, VWAP, EMA on 15m + 30m
# Stocks only. Crypto stays on Adaptive Breakout 4h.
# ═══════════════════════════════════════════════════════════════════

INTRADAY_STRATEGIES = ["rsi_mean_reversion", "macd_crossover", "vwap_bounce", "ema_crossover"]
INTRADAY_TIMEFRAMES = ["15m", "30m"]

# --- RSI Mean Reversion ---
RSI_MR_PERIOD = 14
RSI_MR_OVERSOLD = 30            # Buy when RSI crosses back above 30
RSI_MR_OVERBOUGHT = 70          # Exit when RSI hits 70
RSI_MR_LOOKBACK_BARS = 3        # RSI must have been below 30 within last 3 bars
RSI_MR_STOP_LOSS_PCT = 2.0      # Tight stop for mean reversion
RSI_MR_TAKE_PROFIT_PCT = 3.0    # Modest TP target

# --- MACD Crossover ---
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MACD_MIN_HIST_THRESHOLD = 0.0   # Histogram must be positive after crossover
MACD_STOP_LOSS_PCT = 2.5
MACD_TAKE_PROFIT_PCT = 4.0

# --- VWAP Bounce ---
VWAP_BOUNCE_TOLERANCE_PCT = 0.3   # Price within 0.3% of VWAP
VWAP_BOUNCE_VOLUME_RATIO = 1.2    # Volume must be 1.2x average on bounce
VWAP_BOUNCE_STOP_LOSS_PCT = 1.5   # Tight stop below VWAP
VWAP_BOUNCE_TAKE_PROFIT_PCT = 2.0

# --- EMA Crossover ---
EMA_CROSS_FAST = 9
EMA_CROSS_SLOW = 21
EMA_CROSS_CONFIRMATION_BARS = 2   # Fast must stay above slow for 2 bars
EMA_CROSS_STOP_LOSS_PCT = 2.5
EMA_CROSS_TAKE_PROFIT_PCT = 4.0

# --- Intraday Concurrency & Cooldown ---
MAX_CONCURRENT_INTRADAY_STOCKS = 5
INTRADAY_POST_LOSS_COOLDOWN_BARS = 4  # 4 bars = 1h on 15m, 2h on 30m

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
