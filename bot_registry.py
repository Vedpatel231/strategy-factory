"""Automatic professional bot registry.

The registry creates one bot for every asset + strategy + configured entry
timeframe.  The production default is 1H entries; extra timeframes can be
enabled deliberately through config after paper-trading review.
"""

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional

import config
from alpaca_client import is_equity_symbol, normalize_crypto_symbol
from strategies import STRATEGY_NAMES, create_strategy


ENTRY_TIMEFRAME = getattr(config, "DESK_ENTRY_TIMEFRAME", "1h")
ENTRY_TIMEFRAMES = getattr(config, "DESK_ENTRY_TIMEFRAMES", ["1h"])


def canonical_asset_symbol(asset):
    asset = str(asset or "").upper().replace(" ", "")
    if is_equity_symbol(asset):
        return asset
    if "/" in asset or asset.endswith("USD") or asset.endswith("USDT"):
        return normalize_crypto_symbol(asset)
    return f"{asset}/USD"


def asset_class_for_symbol(symbol):
    return "stock" if is_equity_symbol(symbol) else "crypto"


def db_pair_for_asset(asset):
    asset = str(asset or "").upper().replace(" ", "")
    if is_equity_symbol(asset):
        return asset
    return f"{asset}/USDT"


def _bot_id(symbol, strategy_name, timeframe=ENTRY_TIMEFRAME):
    return f"{symbol}:{strategy_name}:{timeframe}".replace("/", "")


@dataclass
class StrategyBot:
    bot_id: str
    asset: str
    symbol: str
    asset_class: str
    strategy_name: str
    display_name: str
    timeframe: str

    def to_dict(self):
        return asdict(self)

    def create_strategy(self):
        return create_strategy(self.strategy_name)


class BotRegistry:
    def __init__(self, assets: Optional[Iterable[str]] = None,
                 strategy_names: Optional[Iterable[str]] = None,
                 timeframes: Optional[List[str]] = None):
        if assets is None:
            assets = list(config.CRYPTO_ASSETS) + list(config.STOCK_ASSETS)
        self.assets = [str(a).upper().replace(" ", "") for a in assets]
        self.strategy_names = list(strategy_names or STRATEGY_NAMES)
        self.timeframes = timeframes or list(ENTRY_TIMEFRAMES)
        self._bots = self._build_bots()

    def _build_bots(self):
        bots = []
        for asset in self.assets:
            symbol = canonical_asset_symbol(asset)
            asset_class = asset_class_for_symbol(symbol)
            for strategy_name in self.strategy_names:
                strategy = create_strategy(strategy_name)
                for tf in self.timeframes:
                    display = f"{asset} {strategy.display_name} {tf.upper()} Bot"
                    bots.append(StrategyBot(
                        bot_id=_bot_id(symbol, strategy_name, tf),
                        asset=asset,
                        symbol=symbol,
                        asset_class=asset_class,
                        strategy_name=strategy_name,
                        display_name=display,
                        timeframe=tf,
                    ))
        return bots

    def all_bots(self) -> List[StrategyBot]:
        return list(self._bots)

    def bots_for_asset(self, asset_or_symbol) -> List[StrategyBot]:
        symbol = canonical_asset_symbol(asset_or_symbol)
        return [bot for bot in self._bots if bot.symbol == symbol or bot.asset == str(asset_or_symbol).upper()]

    def by_asset(self) -> Dict[str, List[StrategyBot]]:
        grouped = {}
        for bot in self._bots:
            grouped.setdefault(bot.symbol, []).append(bot)
        return grouped

    def summary(self):
        by_asset = self.by_asset()
        return {
            "assets": len(by_asset),
            "bots": len(self._bots),
            "strategies_per_asset": len(self.strategy_names),
            "timeframes": self.timeframes,
            "timeframe": ENTRY_TIMEFRAME,
            "strategy_names": list(self.strategy_names),
            "assets_list": sorted(by_asset.keys()),
        }


def build_registry():
    return BotRegistry()
