"""
Strategy Factory Bot Manager — API Client
Clean wrapper around Binance public API and local SQLite database.
"""
import sqlite3
import logging
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime
import json

from config import BINANCE_BASE_URL, DB_PATH, LOG_FILE, VERBOSE

# === Logging Setup ===
logger = logging.getLogger(__name__)
if VERBOSE:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler()
        ]
    )
else:
    logging.basicConfig(level=logging.WARNING, filename=LOG_FILE)


class StrategyFactoryClient:
    """
    Client for interacting with Binance public API and local bot/strategy database.
    Handles market data fetching and local bot state management.
    """

    def __init__(self):
        """Initialize client with config and DB connection."""
        self.base_url = BINANCE_BASE_URL
        self.db_path = DB_PATH
        self.request_timeout = 30
        self._init_db()
        logger.info("StrategyFactoryClient initialized")

    def _init_db(self) -> None:
        """Verify database exists (tables created by seed_data.py)."""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            # Just verify tables exist — seed_data.py creates the actual schema
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            conn.close()
            if "bots" in tables and "strategies" in tables:
                logger.info("Database initialized successfully")
            else:
                logger.warning("Database tables not found — run seed_data.py first")
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")
            raise

    def _get(self, url: str, params: Optional[Dict] = None) -> Any:
        """
        Reusable HTTP GET with resilient response parsing.

        Args:
            url: Full API endpoint URL
            params: Query parameters

        Returns:
            Parsed response (dict or list)

        Raises:
            requests.RequestException: On network/timeout errors
        """
        try:
            response = requests.get(url, params=params, timeout=self.request_timeout)
            response.raise_for_status()

            # Try to parse JSON
            data = response.json()

            # Handle wrapped responses (try common wrapper keys)
            if isinstance(data, dict):
                for wrapper_key in ["data", "results", "items", "content"]:
                    if wrapper_key in data:
                        logger.debug(f"Unwrapped response via '{wrapper_key}' key")
                        return data[wrapper_key]

            return data

        except requests.exceptions.Timeout:
            logger.error(f"Request timeout for {url}")
            raise
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error for {url}: {e}")
            raise
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error for {url}: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for {url}: {e}")
            raise

    # === Market Data Methods ===

    def get_market_data(
        self, symbol: str, interval: str = "1h", limit: int = 100
    ) -> List[Dict]:
        """
        Fetch OHLCV candle data from Binance.

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            interval: Candle interval (e.g., '1h', '15m', '1d')
            limit: Number of candles to fetch (max 1000)

        Returns:
            List of OHLCV dicts with keys: timestamp, open, high, low, close, volume
        """
        url = f"{self.base_url}/api/v3/klines"
        params = {
            "symbol": symbol.upper(),
            "interval": interval,
            "limit": min(limit, 1000)
        }

        try:
            data = self._get(url, params)

            # Binance returns raw arrays; normalize to dict format
            candles = []
            for candle in data:
                candles.append({
                    "timestamp": int(candle[0]),
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[7])
                })

            logger.info(f"Fetched {len(candles)} candles for {symbol} ({interval})")
            return candles

        except Exception as e:
            logger.error(f"Error fetching market data for {symbol}: {e}")
            return []

    def get_24h_stats(self, symbol: str) -> Dict:
        """
        Fetch 24-hour statistics for a symbol.

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')

        Returns:
            Dict with keys: price, volume_24h, change_pct_24h, high_24h, low_24h
        """
        url = f"{self.base_url}/api/v3/ticker/24hr"
        params = {"symbol": symbol.upper()}

        try:
            data = self._get(url, params)

            result = {
                "symbol": symbol.upper(),
                "price": float(data.get("lastPrice", 0)),
                "volume_24h": float(data.get("volume", 0)),
                "change_pct_24h": float(data.get("priceChangePercent", 0)),
                "high_24h": float(data.get("highPrice", 0)),
                "low_24h": float(data.get("lowPrice", 0))
            }

            logger.debug(f"Fetched 24h stats for {symbol}: {result['price']}")
            return result

        except Exception as e:
            logger.error(f"Error fetching 24h stats for {symbol}: {e}")
            return {}

    def get_all_prices(self) -> Dict[str, float]:
        """
        Fetch current prices for all trading pairs.

        Returns:
            Dict mapping symbol to price
        """
        url = f"{self.base_url}/api/v3/ticker/price"

        try:
            data = self._get(url)

            prices = {}
            if isinstance(data, list):
                for item in data:
                    prices[item["symbol"]] = float(item["price"])
            else:
                logger.warning("Unexpected response format from get_all_prices")

            logger.info(f"Fetched prices for {len(prices)} symbols")
            return prices

        except Exception as e:
            logger.error(f"Error fetching all prices: {e}")
            return {}

    # === Local Bot / Strategy Management ===

    def get_my_bots(self) -> List[Dict]:
        """
        Retrieve all bots from local database.

        Returns:
            List of bot dicts with keys: id, name, strategy_id, status, created_at
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT b.id, b.name, b.strategy_id, b.status, b.created_at, b.last_active,
                       s.pair, s.type as strategy_type, s.timeframe, s.name as strategy_name
                FROM bots b
                LEFT JOIN strategies s ON b.strategy_id = s.id
                ORDER BY b.id
            """)
            rows = cursor.fetchall()
            conn.close()

            bots = [dict(row) for row in rows]
            logger.info(f"Retrieved {len(bots)} bots from database")
            return bots

        except sqlite3.Error as e:
            logger.error(f"Error retrieving bots: {e}")
            return []

    def get_strategy(self, strategy_id: int) -> Dict:
        """
        Retrieve a strategy with aggregated performance metrics from database.

        Args:
            strategy_id: Strategy ID

        Returns:
            Strategy dict with performance metrics
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Get strategy info
            cursor.execute("""
                SELECT id, name, description, type, timeframe, pair, status, created_at
                FROM strategies WHERE id = ?
            """, (strategy_id,))
            row = cursor.fetchone()
            if not row:
                logger.warning(f"Strategy {strategy_id} not found")
                conn.close()
                return {}

            strategy = dict(row)

            # Use the latest performance snapshot for current dashboard metrics.
            # Summing historical cumulative fields like total_trades inflates counts.
            cursor.execute("""
                SELECT
                    win_rate,
                    total_trades,
                    pnl as net_profit,
                    drawdown as max_drawdown,
                    sharpe_ratio,
                    profit_factor,
                    avg_win,
                    avg_loss,
                    consecutive_losses
                FROM performance_history
                WHERE strategy_id = ?
                ORDER BY date DESC
                LIMIT 1
            """, (strategy_id,))
            perf = cursor.fetchone()
            if perf:
                strategy.update({k: perf[k] for k in perf.keys() if perf[k] is not None})

            # Get daily performance history (for equity curves, recent trades analysis)
            cursor.execute("""
                SELECT date, win_rate, total_trades, pnl, drawdown,
                       sharpe_ratio, profit_factor, avg_win, avg_loss, consecutive_losses
                FROM performance_history
                WHERE strategy_id = ?
                ORDER BY date DESC
                LIMIT 30
            """, (strategy_id,))
            history = [dict(r) for r in cursor.fetchall()]
            strategy["performance_history"] = list(reversed(history))

            conn.close()
            return strategy

        except sqlite3.Error as e:
            logger.error(f"Error retrieving strategy {strategy_id}: {e}")
            return {}

    def get_all_strategies(self) -> List[Dict]:
        """
        Retrieve all strategies with performance data from database.

        Returns:
            List of strategy dicts with performance data
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT id FROM strategies ORDER BY id")
            ids = [row["id"] for row in cursor.fetchall()]
            conn.close()

            strategies = [self.get_strategy(sid) for sid in ids]
            strategies = [s for s in strategies if s]

            logger.info(f"Retrieved {len(strategies)} strategies from database")
            return strategies

        except sqlite3.Error as e:
            logger.error(f"Error retrieving all strategies: {e}")
            return []

    def pause_bot(self, bot_id: int) -> Dict:
        """
        Pause a bot by updating its status to 'paused' in database.

        Args:
            bot_id: Bot ID

        Returns:
            Success dict with bot info
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE bots
                SET status = 'paused', last_active = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (bot_id,))

            if cursor.rowcount == 0:
                logger.warning(f"Bot {bot_id} not found for pausing")
                conn.close()
                return {"success": False, "message": f"Bot {bot_id} not found"}

            conn.commit()
            conn.close()

            logger.info(f"Bot {bot_id} paused successfully")
            return {
                "success": True,
                "bot_id": bot_id,
                "status": "paused",
                "timestamp": datetime.utcnow().isoformat()
            }

        except sqlite3.Error as e:
            logger.error(f"Error pausing bot {bot_id}: {e}")
            return {"success": False, "message": str(e)}

    def reactivate_bot(self, bot_id: int) -> Dict:
        """
        Reactivate a paused bot by updating its status to 'active' in database.

        Args:
            bot_id: Bot ID

        Returns:
            Success dict with bot info
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE bots
                SET status = 'active', last_active = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (bot_id,))

            if cursor.rowcount == 0:
                logger.warning(f"Bot {bot_id} not found for reactivation")
                conn.close()
                return {"success": False, "message": f"Bot {bot_id} not found"}

            conn.commit()
            conn.close()

            logger.info(f"Bot {bot_id} reactivated successfully")
            return {
                "success": True,
                "bot_id": bot_id,
                "status": "active",
                "timestamp": datetime.utcnow().isoformat()
            }

        except sqlite3.Error as e:
            logger.error(f"Error reactivating bot {bot_id}: {e}")
            return {"success": False, "message": str(e)}
