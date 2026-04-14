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
