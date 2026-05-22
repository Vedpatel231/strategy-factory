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

# === Asset Universe ===
# DISABLED: No crypto trading.
CRYPTO_ASSETS = []

# Top 10 ETFs — the active trading universe.
# Leveraged/inverse ETFs (SOXL, TQQQ, SOXS) get 50% position sizing.
STOCK_ASSETS = [
    "QQQ",    # Nasdaq 100
    "SPY",    # S&P 500
    "SOXL",   # Semiconductors 3x Bull (LEVERAGED)
    "IWM",    # Russell 2000 Small Cap
    "TQQQ",   # Nasdaq 100 3x Bull (LEVERAGED)
    "SOXX",   # Semiconductors
    "SMH",    # Semiconductors (VanEck)
    "RSP",    # S&P 500 Equal Weight
    "SOXS",   # Semiconductors 3x Bear (LEVERAGED/INVERSE)
    "VOO",    # S&P 500 (Vanguard)
]

# Leveraged/inverse ETFs that need stricter risk controls:
# - 50% of normal position size
# - Tighter open-risk budget
# - Dashboard warning labels
LEVERAGED_ETFS = {"SOXL", "TQQQ", "SOXS"}

# Legacy lists kept for reference (old universe archived)
_ARCHIVED_STOCK_ASSETS = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "LLY", "BRK.B",
    "JPM", "V", "UNH", "XOM", "MA", "COST", "HD", "PG", "JNJ", "ABBV",
    "WMT", "NFLX", "BAC", "CRM", "ORCL", "CVX", "MRK", "KO", "AMD", "PEP",
]
_ARCHIVED_CRYPTO_ASSETS = ["BTC", "ETH", "SOL"]

# === Professional Trading Desk Parameters ===
# Real-money safety default: 1H is the primary entry timeframe.  Shorter
# intraday candles add noise and should only be enabled deliberately after
# paper-trading review.
DESK_ENTRY_TIMEFRAMES = ["1h"]  # bot creation timeframes
DESK_ENTRY_TIMEFRAME = "1h"     # primary entry timeframe
DESK_CONFIRMATION_TIMEFRAMES_INTRADAY = []  # optional future lower-TF context
DESK_CONFIRM_TIMEFRAMES = ["4h", "1D"]
DESK_CYCLE_INTERVAL_MIN = int(os.environ.get("DESK_CYCLE_INTERVAL_MIN", "15"))
PROFESSIONAL_STRATEGIES = [
    "trend_pullback",
    "ema_crossover",
    "macd_momentum",
    "rsi_mean_reversion",
    "bollinger_reversion",
    "breakout_retest",
    "donchian_breakout",
    "vwap_bounce",
    "atr_momentum_expansion",
    "supertrend_continuation",
]

# === Legacy Strategy Parameters (kept for old dashboard/backtest paths) ===
STRATEGY_NAME = "adaptive_breakout"
STRATEGY_TIMEFRAME = "1h"
DONCHIAN_PERIOD = 20          # Donchian channel lookback
ADX_PERIOD = 14               # ADX smoothing period
ADX_ENTRY_THRESHOLD = 20      # Only enter when ADX > 20 (trending)
ADX_EXIT_THRESHOLD = 15       # Exit when ADX drops below 15 (trend dying)
ATR_TRAIL_MULTIPLIER = 3.0    # Trailing stop = 3x ATR(14) from peak
HARD_STOP_PCT = 8.0           # Hard stop loss = 8% from entry
CRYPTO_MIN_ATR_PCT = 0.5      # Min volatility for crypto entries
STOCK_MIN_ATR_PCT = 0.3       # Min volatility for stock entries

# === Concurrency Limits (conservative) ===
MAX_CONCURRENT_CRYPTO = 0     # No crypto trading
MAX_CONCURRENT_STOCKS = 4     # Max 4 ETF positions at once (out of 10 ETFs)

# === Cooldown ===
POST_LOSS_COOLDOWN_BARS = 2   # 2 bars = 8 hours cooldown after a loss

# === Legacy Check Intervals ===
CRYPTO_CHECK_INTERVAL_HOURS = 4
STOCK_CHECK_INTERVAL_HOURS = 4

# ═══════════════════════════════════════════════════════════════════
# Legacy Intraday Stock Strategies
# Disabled by default in alpaca_auto_trader unless ENABLE_LEGACY_INTRADAY=true.
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
