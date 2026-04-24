"""
Strategy Factory Bot Manager — Analytics & Metrics
Resilient strategy performance metric extraction and analysis.
"""
import logging
from typing import Dict, List, Optional, Any
import sqlite3

from config import LOOKBACK_TRADES, VERBOSE

# === Logging Setup ===
logger = logging.getLogger(__name__)
if VERBOSE:
    logger.setLevel(logging.INFO)


def to_float(val: Any, default: float = 0.0) -> float:
    """
    Resilient conversion to float with fallback default.

    Args:
        val: Value to convert
        default: Default if conversion fails

    Returns:
        Float value or default
    """
    if val is None or val == "":
        return default

    try:
        return float(val)
    except (TypeError, ValueError):
        logger.debug(f"Could not convert {val} to float, using default {default}")
        return default


class StrategyMetrics:
    """
    Extracts and normalizes performance metrics from raw strategy data.
    Resilient to missing fields and multiple key naming conventions.
    """

    def __init__(self, raw_data: Dict):
        """
        Initialize metrics from raw strategy data.

        Args:
            raw_data: Raw strategy dict from DB or API
        """
        self.raw_data = raw_data
        self._parse_metrics()

    def _parse_metrics(self) -> None:
        """Parse all metrics from raw data with fallbacks."""
        # Extract stats location (try multiple keys)
        stats = self._get_stats_dict()
        trades_list = self._get_trades_list()

        # Total trades
        self.total_trades = self._extract_total_trades(stats, trades_list)

        # Win rate
        self.win_rate = self._extract_win_rate(stats, trades_list)

        # Profit factor
        self.profit_factor = self._extract_profit_factor(stats, trades_list)

        # Sharpe ratio
        self.sharpe_ratio = self._extract_sharpe_ratio(stats)

        # Max drawdown (negative %)
        self.max_drawdown = self._extract_max_drawdown(stats)

        # Net profit
        self.net_profit = self._extract_net_profit(stats, trades_list)

        # Net profit %
        self.net_profit_pct = self._extract_net_profit_pct(stats, trades_list)

        # Avg win
        self.avg_win = self._extract_avg_win(stats, trades_list)

        # Avg loss (absolute value)
        self.avg_loss = self._extract_avg_loss(stats, trades_list)

        # Consecutive losses
        self.consecutive_losses = self._extract_consecutive_losses(stats, trades_list)

        # Avg loss to avg win ratio
        self.avg_loss_to_avg_win = self._calculate_loss_to_win_ratio()

        # Recent trades analysis
        self.recent_trades = self._extract_recent_trades(trades_list)
        self.recent_win_rate = self._calculate_recent_win_rate()
        self.recent_pnl = self._calculate_recent_pnl()

        logger.debug(f"Parsed metrics for strategy: total_trades={self.total_trades}, "
                     f"win_rate={self.win_rate:.2f}%, profit_factor={self.profit_factor:.2f}")

    def _get_stats_dict(self) -> Dict:
        """
        Extract stats dictionary from raw data.
        Tries multiple key variations.

        Returns:
            Stats dict or empty dict if not found
        """
        for key in ["stats", "liveStats", "live_stats", "performance", "live", "data"]:
            if key in self.raw_data:
                val = self.raw_data[key]
                if isinstance(val, dict):
                    return val

        # If raw_data itself looks like stats, use it
        if isinstance(self.raw_data, dict):
            return self.raw_data

        return {}

    def _get_trades_list(self) -> List[Dict]:
        """
        Extract trades list from raw data.

        Returns:
            List of trade dicts or empty list
        """
        for key in ["trades", "closed_trades", "closedTrades", "recent_trades", "tradeHistory"]:
            if key in self.raw_data:
                val = self.raw_data[key]
                if isinstance(val, list):
                    return val

        # Also check inside stats
        stats = self._get_stats_dict()
        for key in ["trades", "closed_trades", "closedTrades", "tradeHistory"]:
            if key in stats:
                val = stats[key]
                if isinstance(val, list):
                    return val

        return []

    def _extract_total_trades(self, stats: Dict, trades_list: List[Dict]) -> int:
        """Extract total trade count."""
        for key in ["totalTrades", "total_trades", "closedTrades", "numTrades", "num_trades"]:
            if key in stats:
                return max(0, int(to_float(stats[key])))

        # Fallback: count trades list
        return len(trades_list)

    def _extract_win_rate(self, stats: Dict, trades_list: List[Dict]) -> float:
        """Extract win rate percentage."""
        for key in ["winRate", "win_rate", "winRatio", "percentProfitable", "percent_profitable"]:
            if key in stats:
                val = to_float(stats[key])
                # Some APIs return 0-1 scale, others 0-100
                if 0 <= val <= 1:
                    val *= 100
                return val

        # Fallback: calculate from trades
        if trades_list:
            winning_trades = sum(1 for t in trades_list if to_float(t.get("pnl", t.get("profit", 0))) >= 0)
            return (winning_trades / len(trades_list)) * 100

        return 0.0

    def _extract_profit_factor(self, stats: Dict, trades_list: List[Dict]) -> float:
        """Extract profit factor (sum of wins / sum of losses)."""
        for key in ["profitFactor", "profit_factor"]:
            if key in stats:
                return max(0, to_float(stats[key]))

        # Fallback: calculate from trades
        if trades_list:
            gross_profit = 0.0
            gross_loss = 0.0

            for trade in trades_list:
                pnl = to_float(trade.get("pnl", trade.get("profit", 0)))
                if pnl >= 0:
                    gross_profit += pnl
                else:
                    gross_loss += abs(pnl)

            if gross_loss > 0:
                return gross_profit / gross_loss
            elif gross_profit > 0:
                return 1.5  # Convention: all wins, no losses
            return 0.0

        return 0.0

    def _extract_sharpe_ratio(self, stats: Dict) -> float:
        """Extract Sharpe ratio."""
        for key in ["sharpeRatio", "sharpe_ratio", "sharpe"]:
            if key in stats:
                return to_float(stats[key])

        return 0.0

    def _extract_max_drawdown(self, stats: Dict) -> float:
        """Extract max drawdown as negative percentage."""
        for key in ["maxDrawdown", "max_drawdown", "maxDrawdownPct", "max_drawdown_pct", "drawdown"]:
            if key in stats:
                val = to_float(stats[key])
                # Ensure it's negative
                if val > 0:
                    val = -val
                return val

        return 0.0

    def _extract_net_profit(self, stats: Dict, trades_list: List[Dict]) -> float:
        """Extract net profit in currency."""
        for key in ["netProfit", "net_profit", "totalReturn", "total_return", "pnl"]:
            if key in stats:
                return to_float(stats[key])

        # Fallback: sum of all trades
        return sum(to_float(t.get("pnl", t.get("profit", 0))) for t in trades_list)

    def _extract_net_profit_pct(self, stats: Dict, trades_list: List[Dict]) -> float:
        """Extract net profit as percentage."""
        for key in ["netProfitPct", "net_profit_pct", "returnPct", "return_pct", "totalReturnPct"]:
            if key in stats:
                val = to_float(stats[key])
                # Some APIs return decimal (0.05), others percentage (5.0)
                if -100 < val < 100 and abs(val) < 1:
                    val *= 100
                return val

        return 0.0

    def _extract_avg_win(self, stats: Dict, trades_list: List[Dict]) -> float:
        """Extract average winning trade."""
        for key in ["avgWin", "avg_win", "averageWin", "average_win"]:
            if key in stats:
                return max(0, to_float(stats[key]))

        # Fallback: calculate from trades
        if trades_list:
            winning_trades = [to_float(t.get("pnl", t.get("profit", 0)))
                            for t in trades_list
                            if to_float(t.get("pnl", t.get("profit", 0))) > 0]
            if winning_trades:
                return sum(winning_trades) / len(winning_trades)

        return 0.0

    def _extract_avg_loss(self, stats: Dict, trades_list: List[Dict]) -> float:
        """Extract average losing trade (absolute value)."""
        for key in ["avgLoss", "avg_loss", "averageLoss", "average_loss"]:
            if key in stats:
                return abs(to_float(stats[key]))

        # Fallback: calculate from trades
        if trades_list:
            losing_trades = [abs(to_float(t.get("pnl", t.get("profit", 0))))
                           for t in trades_list
                           if to_float(t.get("pnl", t.get("profit", 0))) < 0]
            if losing_trades:
                return sum(losing_trades) / len(losing_trades)

        return 0.0

    def _extract_consecutive_losses(self, stats: Dict, trades_list: List[Dict]) -> int:
        """Extract maximum consecutive losses."""
        for key in ["maxConsecutiveLosses", "max_consecutive_losses", "consecutiveLosses", "consecutive_losses"]:
            if key in stats:
                return max(0, int(to_float(stats[key])))

        # Fallback: calculate from trades
        if trades_list:
            max_consecutive = 0
            current_consecutive = 0

            for trade in trades_list:
                pnl = to_float(trade.get("pnl", trade.get("profit", 0)))
                if pnl < 0:
                    current_consecutive += 1
                    max_consecutive = max(max_consecutive, current_consecutive)
                else:
                    current_consecutive = 0

            return max_consecutive

        return 0

    def _calculate_loss_to_win_ratio(self) -> float:
        """Calculate avg_loss / avg_win ratio."""
        if self.avg_win > 0:
            return self.avg_loss / self.avg_win
        return 0.0

    def _extract_recent_trades(self, trades_list: List[Dict]) -> List[Dict]:
        """Extract last N trades (most recent)."""
        return trades_list[-LOOKBACK_TRADES:] if trades_list else []

    def _normalize_recent_trades(self) -> List[Dict]:
        """
        Export recent trades in a stable shape for decision/learning logic.

        Upstream sources are inconsistent and often expose only `pnl` or
        `profit`, while downstream code expects a `win` boolean.
        """
        normalized = []
        for trade in self.recent_trades:
            pnl = to_float(trade.get("pnl", trade.get("profit", 0)))
            normalized.append({
                "pnl": round(pnl, 4),
                "win": pnl >= 0,
            })
        return normalized

    def _calculate_recent_win_rate(self) -> float:
        """Calculate win rate for recent trades only."""
        if not self.recent_trades:
            return 0.0

        wins = sum(1 for t in self.recent_trades
                   if to_float(t.get("pnl", t.get("profit", 0))) >= 0)
        return (wins / len(self.recent_trades)) * 100

    def _calculate_recent_pnl(self) -> float:
        """Calculate total PnL for recent trades."""
        if not self.recent_trades:
            return 0.0

        return sum(to_float(t.get("pnl", t.get("profit", 0))) for t in self.recent_trades)

    # === Properties & Output ===

    def to_dict(self) -> Dict:
        """
        Export all metrics as a clean dictionary.

        Returns:
            Dict with all normalized metrics
        """
        return {
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 2),
            "profit_factor": round(self.profit_factor, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "net_profit": round(self.net_profit, 2),
            "net_profit_pct": round(self.net_profit_pct, 2),
            "avg_win": round(self.avg_win, 2),
            "avg_loss": round(self.avg_loss, 2),
            "consecutive_losses": self.consecutive_losses,
            "avg_loss_to_avg_win": round(self.avg_loss_to_avg_win, 2),
            "recent_trades": self._normalize_recent_trades(),
            "recent_trades_count": len(self.recent_trades),
            "recent_win_rate": round(self.recent_win_rate, 2),
            "recent_pnl": round(self.recent_pnl, 2)
        }

    def summary(self) -> str:
        """
        Generate human-readable summary of metrics.

        Returns:
            Formatted summary string
        """
        lines = [
            "=== Strategy Performance Summary ===",
            f"Total Trades: {self.total_trades}",
            f"Win Rate: {self.win_rate:.2f}%",
            f"Profit Factor: {self.profit_factor:.2f}x",
            f"Sharpe Ratio: {self.sharpe_ratio:.2f}",
            f"Max Drawdown: {self.max_drawdown:.2f}%",
            f"Net Profit: ${self.net_profit:,.2f} ({self.net_profit_pct:.2f}%)",
            f"Avg Win: ${self.avg_win:,.2f}",
            f"Avg Loss: ${self.avg_loss:,.2f}",
            f"Avg Loss/Win Ratio: {self.avg_loss_to_avg_win:.2f}x",
            f"Max Consecutive Losses: {self.consecutive_losses}",
            "",
            f"Recent Trades (last {len(self.recent_trades)}): {self.recent_win_rate:.2f}% win rate, ${self.recent_pnl:,.2f} PnL"
        ]
        return "\n".join(lines)

    @classmethod
    def from_db_row(cls, row: sqlite3.Row) -> "StrategyMetrics":
        """
        Create StrategyMetrics from a database row.

        Args:
            row: sqlite3.Row with strategy data

        Returns:
            StrategyMetrics instance
        """
        import json

        raw_data = {
            "id": row["id"],
            "bot_id": row["bot_id"],
            "name": row["name"],
            "status": row["status"],
            "total_trades": row["total_trades"],
            "win_rate": row["win_rate"],
            "profit_factor": row["profit_factor"],
            "sharpe_ratio": row["sharpe_ratio"],
            "max_drawdown": row["max_drawdown"],
            "net_profit": row["net_profit"],
            "net_profit_pct": row["net_profit_pct"],
            "consecutive_losses": row["consecutive_losses"],
            "last_updated": row["last_updated"]
        }

        # Parse performance_data if present
        if row["performance_data"]:
            try:
                raw_data["stats"] = json.loads(row["performance_data"])
            except (json.JSONDecodeError, TypeError):
                pass

        return cls(raw_data)
