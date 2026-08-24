"""
Strategy Factory — Configuration
ETF-only trading system. All tunable settings live here.
Paths respect STRATEGY_FACTORY_DATA_DIR env var for Railway persistent volume.
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

# === OPTIONS MODE (2026-08 pivot: cash-secured put seller / wheel) ===
# The system is migrating from stock/ETF swing trading to an options
# premium-selling bot. When OPTIONS_MODE is True the stock desk stops opening
# new equity positions; the options engine trades instead. (Set OPTIONS_MODE=0
# to fall back to the legacy stock desk while it still exists.)
OPTIONS_MODE = os.environ.get("OPTIONS_MODE", "1") not in ("0", "false", "False", "no")

# Underlyings for cash-secured puts — low-priced, liquid names so one contract's
# collateral (~strike x 100) fits a small account.
OPTIONS_UNDERLYINGS = [
    "SOFI",   # ~$19  -> ~$1.9k collateral, higher IV
    "PFE",    # ~$28  -> ~$2.8k collateral, steady dividend payer
    "T",      # ~$26  -> ~$2.6k collateral, low-vol telecom
    "F",      # ~$14  -> ~$1.4k collateral, very liquid
]

# Put-seller parameters (all env-overridable; tuned in Stage 2).
OPT_TARGET_DELTA = float(os.environ.get("OPT_TARGET_DELTA", "0.30"))          # sell ~30-delta puts
OPT_DELTA_TOLERANCE = float(os.environ.get("OPT_DELTA_TOLERANCE", "0.12"))
OPT_MIN_DTE = int(os.environ.get("OPT_MIN_DTE", "5"))                         # avoid 0-4 DTE gamma risk
OPT_MAX_DTE = int(os.environ.get("OPT_MAX_DTE", "14"))                        # up to ~2 weeks
OPT_TARGET_DTE = int(os.environ.get("OPT_TARGET_DTE", "9"))                   # prefer the weekly nearest this
OPT_PROFIT_TAKE_PCT = float(os.environ.get("OPT_PROFIT_TAKE_PCT", "0.50"))    # buy back at 50% of credit
OPT_MIN_IV_PCT = float(os.environ.get("OPT_MIN_IV_PCT", "15"))               # only sell when IV rich enough
OPT_MAX_POSITIONS = int(os.environ.get("OPT_MAX_POSITIONS", "1"))            # max concurrent short puts (start with 1)
OPT_MAX_TOTAL_COLLATERAL = float(os.environ.get("OPT_MAX_TOTAL_COLLATERAL", "0"))  # $ cap on total collateral (0 = use full buying power)
OPT_MAX_CONTRACTS_PER_NAME = int(os.environ.get("OPT_MAX_CONTRACTS_PER_NAME", "1"))
OPT_ROLL_DTE = int(os.environ.get("OPT_ROLL_DTE", "1"))                       # manage/roll when <= this DTE
OPT_COVERED_CALL_DELTA = float(os.environ.get("OPT_COVERED_CALL_DELTA", "0.30"))  # wheel: covered-call delta
# Safety switch: the options desk runs in DECISION-ONLY (dry-run) mode until
# OPTIONS_LIVE is explicitly turned on. Dry-run computes and logs every action
# it would take on real data but places NO orders.
OPTIONS_LIVE = os.environ.get("OPTIONS_LIVE", "0") not in ("0", "false", "False", "no")

# Active trading universe — 20 liquid, long-only swing-trading names:
# 10 broad/sector ETFs + 10 mega-cap stocks.  Chosen for deep liquidity,
# tight spreads, and clean trends, with NO leveraged/inverse products so
# position sizing stays clean.  Narrow single-sector ETFs that whipsawed
# (XLV, XLI, XBI, GDX) were removed in the 2026-07 audit.
#
# NOTE: single stocks can GAP on earnings (overnight moves past the stop).
# The bot holds overnight, so an earnings-blackout rule is the recommended
# next safety add-on before real money (not yet implemented).
STOCK_ASSETS = [
    # --- Broad & sector ETFs (10) ---
    "SPY",    # S&P 500 (most liquid instrument in the world)
    "QQQ",    # Nasdaq 100 (megacap tech / growth)
    "IWM",    # Russell 2000 (small cap — different beta)
    "DIA",    # Dow 30 (large-cap industrials tilt)
    "SMH",    # Semiconductors (high-beta secular trend)
    "XLK",    # Technology sector
    "XLE",    # Energy (clean cyclical trends)
    "XLF",    # Financials
    "GLD",    # Gold (low equity correlation / risk-off)
    "TLT",    # 20+yr Treasuries (rates / risk-off diversifier)
    # --- Mega-cap stocks (10) — highest volume, trend cleanly ---
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "NVDA",   # Nvidia
    "GOOGL",  # Alphabet (Google)
    "AMZN",   # Amazon
    "META",   # Meta
    "TSLA",   # Tesla
    "AMD",    # AMD
    "AVGO",   # Broadcom
    "NFLX",   # Netflix
]

# Leveraged/inverse ETFs that need stricter risk controls (50% size,
# tighter open-risk budget, dashboard warning labels).  The current
# universe is long-only with NO leverage, so this set is intentionally
# empty.  The handling code remains in place and simply does nothing
# while no leveraged tickers are traded.
LEVERAGED_ETFS = set()

# (Old stock/crypto asset lists moved to archive/)

# === Professional Trading Desk Parameters ===
# Real-money safety default: 1H is the primary entry timeframe.  Shorter
# intraday candles add noise and should only be enabled deliberately after
# paper-trading review.
DESK_ENTRY_TIMEFRAMES = ["1h"]  # bot creation timeframes
DESK_ENTRY_TIMEFRAME = "1h"     # primary entry timeframe
DESK_CONFIRMATION_TIMEFRAMES_INTRADAY = []  # optional future lower-TF context
DESK_CONFIRM_TIMEFRAMES = ["4h", "1D"]
DESK_CYCLE_INTERVAL_MIN = int(os.environ.get("DESK_CYCLE_INTERVAL_MIN", "15"))
# Active strategy set. Trimmed 2026-07 after a 20-symbol backtest dropped the
# strategies with no edge: trend_pullback (negative expectancy), vwap_bounce
# (~no edge) and supertrend_continuation (~flat). Their classes remain in
# strategies/professional_strategies.py so they can be re-tested or restored
# by adding the name back here — nothing is deleted.
PROFESSIONAL_STRATEGIES = [
    "donchian_breakout",       # backtest PF 1.62, +1.59%/trade (best evidence)
    "bollinger_reversion",     # PF 3.16, +2.46%/trade
    "breakout_retest",         # PF 2.03, +1.96%/trade
    "atr_momentum_expansion",  # PF 1.38, +1.02%/trade
    "rsi_mean_reversion",      # PF 3.23, +2.34%/trade (smaller sample)
    "ema_crossover",           # PF 3.65, +2.53%/trade (small sample — on watch)
    "macd_momentum",           # PF 1.13, +0.30%/trade (marginal — on watch)
]

# Strategies removed from the active set on 2026-07 (kept here for the record;
# re-add to PROFESSIONAL_STRATEGIES to restore): trend_pullback, vwap_bounce,
# supertrend_continuation.
DISABLED_STRATEGIES_NOTE = ["trend_pullback", "vwap_bounce", "supertrend_continuation"]

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
MAX_CONCURRENT_STOCKS = 4     # Max 4 positions at once (out of 20-name universe)

# === Cooldown ===
POST_LOSS_COOLDOWN_BARS = 2   # 2 bars = 8 hours cooldown after a loss

# === Let-Winners-Run profit protection (2026-07 audit fix) ===
# Problem the audit found: winners were force-closed tiny (avg +$10) while
# losers ran to the stop (avg -$26), giving a losing 0.41 win/loss ratio.
# These knobs let a winning trade keep running instead of being clipped,
# while still protecting the open gain.  All are env-overridable and the
# EOD behavior is fully reversible (set EOD_LET_WINNERS_RUN=0).
PROFIT_LOCK_PCT = float(os.environ.get("PROFIT_LOCK_PCT", "0.5"))
# Above this gain %, "soft" exits (signal invalidation, regime flip) stop
# force-closing a winner — the trailing stop / take-profit manage it instead.
PROFIT_PROTECT_PCT = float(os.environ.get("PROFIT_PROTECT_PCT", "1.0"))
# At/above this gain %, ratchet the stop up to break-even so a winner can
# keep running but can no longer round-trip into a loss.
EOD_LET_WINNERS_RUN = os.environ.get("EOD_LET_WINNERS_RUN", "1") not in ("0", "false", "False", "no")
# When True, EOD no longer blanket-closes every green position; it locks a
# protective stop and holds the winner so it can run over the following days.
EOD_GAIN_LOCK_FRACTION = float(os.environ.get("EOD_GAIN_LOCK_FRACTION", "0.5"))
# Fraction of the open gain the EOD protective stop locks in (never below
# break-even). 0.5 = lock half the gain, let the rest run.

# === Strategy Parameters (used by professional_strategies.py) ===

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

# --- Intraday Concurrency ---
MAX_CONCURRENT_INTRADAY_STOCKS = 5

# === Market-trend filter ("don't fight the tape", 2026-07 audit) ===
# Block NEW long entries while the broad market is below its trend MA.
# Existing positions are unaffected. Reversible via MARKET_FILTER_ENABLED=0.
MARKET_FILTER_ENABLED = os.environ.get("MARKET_FILTER_ENABLED", "1") not in ("0", "false", "False", "no")
MARKET_FILTER_SYMBOL = os.environ.get("MARKET_FILTER_SYMBOL", "SPY")
MARKET_FILTER_MA_PERIOD = int(os.environ.get("MARKET_FILTER_MA_PERIOD", "50"))
# Gate mode: "50ma" (price >= 50D SMA), "200ma" (price >= 200D SMA),
# or "50and200" (both). Default 50ma balances protection vs staying active.
MARKET_FILTER_MODE = os.environ.get("MARKET_FILTER_MODE", "50ma")

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
