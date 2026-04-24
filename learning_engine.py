"""
Adaptive Learning Engine for Crypto Trading Bot Management System

The adaptive brain that tracks market regimes, strategy performance within regimes,
and validates whether past decisions were correct. The system improves over time through
hindsight analysis and regime-aware strategy evaluation.
"""

import math
import json
import os
from datetime import datetime

try:
    import numpy as np
except ImportError:
    np = None

import config


class LearningEngine:
    """
    Manages persistent learning state, regime detection, and adaptive verdict enhancement.
    """

    def __init__(self, learning_state_file=None):
        """
        Initialize the learning engine.

        Args:
            learning_state_file (str): Path to persistence file. Defaults to config.LEARNING_STATE_FILE
        """
        self.learning_state_file = learning_state_file or config.LEARNING_STATE_FILE
        self.state = self._initialize_state()
        self.load_state()

    def _initialize_state(self):
        """Create fresh learning state structure."""
        return {
            "version": 1,
            "strategies": {},
            "regime_history": [],
            "current_regime": "unknown",
            "calibration": {
                "pause_regret_rate": 0.0,
                "total_decisions": 0,
                "correct_decisions": 0,
            },
        }

    def _empty_regime_stats(self):
        return {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0, "profit_factor": 0}

    def _default_seeded_regime_performance(self):
        return {
            "trending_up": self._empty_regime_stats(),
            "trending_down": self._empty_regime_stats(),
            "mean_reverting": self._empty_regime_stats(),
            "high_volatility": self._empty_regime_stats(),
            "low_volatility": self._empty_regime_stats(),
            "choppy": self._empty_regime_stats(),
            "unknown": self._empty_regime_stats(),
        }

    def _default_real_regime_performance(self):
        return {
            "trending_up": self._empty_regime_stats(),
            "trending_down": self._empty_regime_stats(),
            "range_bound": self._empty_regime_stats(),
            "choppy": self._empty_regime_stats(),
            "breakout": self._empty_regime_stats(),
            "breakdown": self._empty_regime_stats(),
            "high_volatility": self._empty_regime_stats(),
            "extreme_volatility": self._empty_regime_stats(),
            "unknown": self._empty_regime_stats(),
        }

    def _normalize_real_regime(self, regime):
        if not regime:
            return "unknown"
        regime = str(regime).strip().lower()
        aliases = {
            "mean_reverting": "range_bound",
            "low_volatility": "range_bound",
        }
        regime = aliases.get(regime, regime)
        if regime in self._default_real_regime_performance():
            return regime
        return "unknown"

    def load_state(self):
        """Load learning state from disk if it exists."""
        if os.path.exists(self.learning_state_file):
            try:
                with open(self.learning_state_file, "r") as f:
                    saved_state = json.load(f)
                    if saved_state.get("version") == 1:
                        self.state = saved_state
            except (json.JSONDecodeError, IOError):
                # File corrupted or unreadable; keep fresh state
                pass

    def save_state(self):
        """Persist learning state to disk."""
        os.makedirs(os.path.dirname(self.learning_state_file), exist_ok=True)
        with open(self.learning_state_file, "w") as f:
            json.dump(self.state, f, indent=2)

    def get_strategy_state(self, strategy_id):
        """
        Get or create strategy state entry.

        Args:
            strategy_id (str): Unique strategy identifier

        Returns:
            dict: Strategy state with regime_performance, adaptation_history, pause_events
        """
        if strategy_id not in self.state["strategies"]:
            self.state["strategies"][strategy_id] = {
                "regime_performance": self._default_seeded_regime_performance(),
                "real_regime_performance": self._default_real_regime_performance(),
                "real_symbol_performance": {},
                "adaptation_history": [],
                "pause_events": [],
            }
        strategy_state = self.state["strategies"][strategy_id]
        strategy_state.setdefault("regime_performance", self._default_seeded_regime_performance())
        strategy_state.setdefault("real_regime_performance", self._default_real_regime_performance())
        strategy_state.setdefault("real_symbol_performance", {})
        for regime_name, empty_stats in self._default_real_regime_performance().items():
            strategy_state["real_regime_performance"].setdefault(regime_name, dict(empty_stats))
        return strategy_state

    # ── PHASE 6 FIX: Feed REAL Alpaca trade outcomes ──────────────
    def record_real_trade(self, strategy_id, regime, net_pl, symbol=None, save=True):
        """
        Record a real Alpaca paper trade outcome into the learning state.
        This is the critical link between real performance and strategy adaptation.

        Args:
            strategy_id: Strategy name (e.g. 'pullback_continuation', 'breakout')
            regime: Market regime when trade was entered
            net_pl: Net P&L after fees (positive = win, negative = loss)
            symbol: Trading pair (optional, for per-symbol tracking)
        """
        strategy_state = self.get_strategy_state(strategy_id)

        regime_key = self._normalize_real_regime(regime)
        perf = strategy_state["real_regime_performance"][regime_key]

        perf["trades"] += 1
        perf["pnl"] = round(perf["pnl"] + net_pl, 2)
        if net_pl > 0:
            perf["wins"] += 1
        perf["win_rate"] = round(perf["wins"] / perf["trades"] * 100, 1) if perf["trades"] > 0 else 0

        # Track per-symbol performance
        if symbol:
            sym_perf = strategy_state["real_symbol_performance"].setdefault(
                symbol, {"trades": 0, "wins": 0, "pnl": 0}
            )
            sym_perf["trades"] += 1
            sym_perf["pnl"] = round(sym_perf["pnl"] + net_pl, 2)
            if net_pl > 0:
                sym_perf["wins"] += 1

        if save:
            self.save_state()

    def get_strategy_real_win_rate(self, strategy_id, regime=None):
        """Get real win rate for a strategy, optionally filtered by regime."""
        strategy_state = self.get_strategy_state(strategy_id)
        if regime:
            regime_key = self._normalize_real_regime(regime)
            perf = strategy_state["real_regime_performance"][regime_key]
            return perf.get("win_rate", 0), perf.get("trades", 0)
        # Overall across all regimes
        total_trades = sum(p["trades"] for p in strategy_state["real_regime_performance"].values())
        total_wins = sum(p["wins"] for p in strategy_state["real_regime_performance"].values())
        if total_trades == 0:
            return 0, 0
        return round(total_wins / total_trades * 100, 1), total_trades

    def should_block_strategy(self, strategy_id, regime):
        """
        PHASE 6: Return True if this strategy has proven to lose in this regime.
        Requires at least 5 trades to have statistical confidence.
        """
        win_rate, trades = self.get_strategy_real_win_rate(strategy_id, regime)
        if trades >= 5 and win_rate < 30:
            return True, f"Strategy '{strategy_id}' has {win_rate}% real win rate in '{regime}' ({trades} trades)"
        return False, ""

    def ingest_trade_ledger(self):
        """
        PHASE 6: Bulk-import closed trades from the trade ledger CSV into
        the learning state.  Idempotent — skips already-counted trade IDs.
        """
        try:
            from trade_journal import load_trade_ledger
            rows = load_trade_ledger(limit=500)
            imported_ids = set(self.state.get("imported_trade_ids") or [])

            new_count = 0
            for row in rows:
                trade_id = row.get("trade_id", "")
                # Fallback ID when trade_id is missing — prevents all rows
                # colliding on empty string and only first one being imported.
                if not trade_id:
                    trade_id = f"{row.get('symbol', 'UNK')}-{row.get('closed_at', row.get('timestamp', ''))}"
                if not trade_id or trade_id in imported_ids:
                    continue
                strategy = row.get("strategy", "unknown")
                regime = row.get("regime", "unknown")
                try:
                    net_pl = float(row.get("net_pl", 0) or 0)
                except (TypeError, ValueError):
                    net_pl = 0
                symbol = row.get("symbol", "")
                self.record_real_trade(strategy, regime, net_pl, symbol, save=False)
                imported_ids.add(trade_id)
                new_count += 1

            self.state["imported_trade_ids"] = sorted(imported_ids)
            if new_count:
                self.save_state()
            return new_count
        except Exception as e:
            import logging
            logging.getLogger("learning_engine").warning(f"Trade ledger ingestion failed: {e}")
            return 0

    def detect_regime(self, equity_curves):
        """
        Detect current market regime using Markov regime classification.

        Args:
            equity_curves (list[list[float]]): PnL values per strategy (last 20 trades each)

        Returns:
            dict with keys:
                - regime: str (regime classification)
                - confidence: float (0-1)
                - stats: dict (mean, variance, std_dev, autocorr, cv, etc.)
                - transition_probs: dict (regime transition probabilities)
        """
        if not equity_curves or all(len(curve) == 0 for curve in equity_curves):
            return {
                "regime": "unknown",
                "confidence": 0.0,
                "stats": {},
                "transition_probs": {},
            }

        # Flatten and compute statistics
        combined_pnl = []
        for curve in equity_curves:
            combined_pnl.extend(curve)

        if len(combined_pnl) < 3:
            return {
                "regime": "unknown",
                "confidence": 0.0,
                "stats": {"insufficient_data": True},
                "transition_probs": {},
            }

        stats = self._compute_regime_stats(combined_pnl)

        # Classify regime
        regime_scores = self._classify_regime(stats)
        regime = regime_scores["best_regime"]
        confidence = regime_scores["confidence"]

        # Regime persistence: require 3 consecutive identical readings before switching
        recent_regimes = [entry["regime"] for entry in self.state["regime_history"][-2:]]
        if len(recent_regimes) >= 2:
            candidate_regimes = recent_regimes + [regime]
            if not all(r == regime for r in candidate_regimes):
                # Last 3 readings (including current) are not unanimous; keep previous regime
                regime = self.state.get("current_regime", regime)
                confidence = min(confidence, 0.5)

        # Track transitions
        transition_probs = self._compute_transition_probs(regime)

        # Update regime history
        self.state["regime_history"].append(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "regime": regime,
                "confidence": confidence,
            }
        )
        self.state["current_regime"] = regime

        return {
            "regime": regime,
            "confidence": confidence,
            "stats": stats,
            "transition_probs": transition_probs,
        }

    def _compute_regime_stats(self, pnl_values):
        """Compute statistical features for regime detection."""
        if np is None:
            pnl_array = pnl_values
            mean_return = sum(pnl_array) / len(pnl_array)
            variance = sum((x - mean_return) ** 2 for x in pnl_array) / len(pnl_array)
            std_dev = math.sqrt(variance)
        else:
            pnl_array = np.array(pnl_values)
            mean_return = float(np.mean(pnl_array))
            variance = float(np.var(pnl_array))
            std_dev = float(np.std(pnl_array))

        # Coefficient of variation
        cv = (std_dev / abs(mean_return)) if mean_return != 0 else float("inf")

        # Lag-1 autocorrelation
        if len(pnl_values) > 1:
            mean = sum(pnl_values) / len(pnl_values)
            numerator = sum((pnl_values[i] - mean) * (pnl_values[i + 1] - mean)
                          for i in range(len(pnl_values) - 1))
            denominator = sum((x - mean) ** 2 for x in pnl_values)
            autocorr = numerator / denominator if denominator != 0 else 0.0
        else:
            autocorr = 0.0

        # Win/loss streaks
        wins = sum(1 for x in pnl_values if x > 0)
        losses = sum(1 for x in pnl_values if x < 0)

        max_win_streak = self._max_consecutive(pnl_values, positive=True)
        max_loss_streak = self._max_consecutive(pnl_values, positive=False)

        return {
            "mean_return": mean_return,
            "variance": variance,
            "std_dev": std_dev,
            "autocorrelation": autocorr,
            "coefficient_of_variation": cv,
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "total_values": len(pnl_values),
            "win_ratio": wins / len(pnl_values) if pnl_values else 0,
        }

    def _max_consecutive(self, values, positive=True):
        """Find longest consecutive streak of positive or negative values."""
        max_streak = 0
        current_streak = 0
        for val in values:
            is_positive = val > 0
            if (positive and is_positive) or (not positive and not is_positive):
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0
        return max_streak

    def _classify_regime(self, stats):
        """Classify regime based on computed statistics."""
        if stats.get("insufficient_data"):
            return {"best_regime": "unknown", "confidence": 0.0}

        mean_ret = stats["mean_return"]
        autocorr = stats["autocorrelation"]
        cv = stats["coefficient_of_variation"]
        std_dev = stats["std_dev"]

        regime_scores = {}

        # Trending up
        if autocorr > 0.3 and mean_ret > 0:
            regime_scores["trending_up"] = 0.8
        else:
            regime_scores["trending_up"] = 0.0

        # Trending down
        if autocorr > 0.3 and mean_ret < 0:
            regime_scores["trending_down"] = 0.8
        else:
            regime_scores["trending_down"] = 0.0

        # Mean reverting
        if autocorr < -0.2:
            regime_scores["mean_reverting"] = 0.75
        else:
            regime_scores["mean_reverting"] = 0.0

        # High volatility
        if cv > 5:
            regime_scores["high_volatility"] = 0.7
        else:
            regime_scores["high_volatility"] = 0.0

        # Low volatility
        if std_dev < 0.5:
            regime_scores["low_volatility"] = 0.7
        else:
            regime_scores["low_volatility"] = 0.0

        # Choppy (neutral between mean revert and trending)
        if 1.0 <= cv <= 3.0 and abs(autocorr) < 0.2:
            regime_scores["choppy"] = 0.6
        else:
            regime_scores["choppy"] = 0.0

        # Find best match
        best_regime = max(regime_scores, key=regime_scores.get) if regime_scores else "unknown"
        best_score = regime_scores.get(best_regime, 0.0)

        # Fall back to unknown if no strong signals
        if best_score < 0.3:
            best_regime = "unknown"
            best_score = 0.0

        return {"best_regime": best_regime, "confidence": best_score}

    def _compute_transition_probs(self, current_regime):
        """Compute Markov transition probabilities from regime history."""
        if len(self.state["regime_history"]) < 2:
            return {}

        transitions = {}
        for i in range(len(self.state["regime_history"]) - 1):
            from_regime = self.state["regime_history"][i]["regime"]
            to_regime = self.state["regime_history"][i + 1]["regime"]

            if from_regime not in transitions:
                transitions[from_regime] = {}

            transitions[from_regime][to_regime] = transitions[from_regime].get(to_regime, 0) + 1

        # Normalize to probabilities
        probs = {}
        for from_regime, destinations in transitions.items():
            total = sum(destinations.values())
            probs[from_regime] = {
                to_regime: count / total for to_regime, count in destinations.items()
            }

        return probs

    @staticmethod
    def _trade_is_win(trade):
        """Interpret recent-trade rows from normalized or raw source shapes."""
        if "win" in trade:
            return bool(trade.get("win"))
        pnl = trade.get("pnl", trade.get("profit", 0))
        try:
            return float(pnl) >= 0
        except (TypeError, ValueError):
            return False

    def compute_adaptation_score(self, metrics, regime, strategy_id):
        """
        Compute adaptive fitness score for a strategy in current regime.

        Args:
            metrics (dict): Current strategy metrics
            regime (str): Current regime classification
            strategy_id (str): Strategy identifier

        Returns:
            dict with:
                - score: int (0-100)
                - label: str (WELL_ADAPTED, MODERATELY_ADAPTED, etc.)
                - breakdown: dict (component scores)
        """
        score = 50  # Start neutral
        breakdown = {}

        strategy_state = self.get_strategy_state(strategy_id)
        regime_perf = strategy_state["regime_performance"].get(regime, {})

        # Component 1: Regime-specific performance
        regime_trades = regime_perf.get("trades", 0)
        if regime_trades >= 3:
            confidence = min(1.0, regime_trades / 10)
            win_rate = metrics.get("win_rate", 0)
            if win_rate > 55:
                adj = int(15 * confidence)
                score += adj
                breakdown["regime_performance"] = adj
            elif win_rate < 42:
                adj = int(-20 * confidence)
                score += adj
                breakdown["regime_performance"] = adj
            else:
                breakdown["regime_performance"] = 0

            profit_factor = metrics.get("profit_factor", 0)
            if profit_factor > 1.3:
                adj = int(10 * confidence)
                score += adj
                breakdown["profit_factor"] = adj
            elif profit_factor < 0.9:
                adj = int(-15 * confidence)
                score += adj
                breakdown["profit_factor"] = adj
            else:
                breakdown["profit_factor"] = 0
        else:
            breakdown["regime_performance"] = 0
            breakdown["profit_factor"] = 0

        # Component 2: Performance trajectory
        recent_trades = metrics.get("recent_trades", [])
        overall_win_rate = metrics.get("win_rate", 50)
        if len(recent_trades) >= 10:
            recent_wins = sum(1 for t in recent_trades[-10:] if self._trade_is_win(t))
            recent_rate = (recent_wins / 10) * 100
            diff = recent_rate - overall_win_rate

            if diff > 8:
                score += 12
                breakdown["trajectory"] = 12
            elif diff < -8:
                score -= 12
                breakdown["trajectory"] = -12
            elif abs(diff) <= 3:
                score += 5
                breakdown["trajectory"] = 5
            else:
                breakdown["trajectory"] = 0
        else:
            breakdown["trajectory"] = 0

        # Component 3: Edge stability (compare halves of recent trades)
        if len(recent_trades) >= 4:
            mid = len(recent_trades) // 2
            first_half_wins = sum(1 for t in recent_trades[:mid] if self._trade_is_win(t))
            second_half_wins = sum(1 for t in recent_trades[mid:] if self._trade_is_win(t))

            first_half_rate = (first_half_wins / mid * 100) if mid > 0 else 50
            second_half_rate = (second_half_wins / (len(recent_trades) - mid) * 100) if (len(recent_trades) - mid) > 0 else 50
            variance = abs(first_half_rate - second_half_rate)

            if variance > 25:
                score -= 10
                breakdown["stability"] = -10
            elif variance < 8:
                score += 5
                breakdown["stability"] = 5
            else:
                breakdown["stability"] = 0
        else:
            breakdown["stability"] = 0

        # Component 4: Statistical significance (expected vs actual loss streaks)
        win_rate = metrics.get("win_rate", 50) / 100
        consecutive_losses = metrics.get("consecutive_losses", 0)
        if win_rate > 0 and win_rate < 1:
            expected_streak = math.log(regime_trades) / math.log(1 / (1 - win_rate)) if regime_trades > 0 else 1
            actual_to_expected = consecutive_losses / expected_streak if expected_streak > 0 else 0

            if actual_to_expected > 1.5:
                score -= 15
                breakdown["significance"] = -15
            elif actual_to_expected < 0.8:
                score += 5
                breakdown["significance"] = 5
            else:
                breakdown["significance"] = 0
        else:
            breakdown["significance"] = 0

        # Component 5: Expected value
        avg_win = metrics.get("avg_win", 0)
        avg_loss = metrics.get("avg_loss", 0)
        win_rate_decimal = metrics.get("win_rate", 50) / 100
        ev = (win_rate_decimal * avg_win) - ((1 - win_rate_decimal) * avg_loss)

        if ev > 0:
            score += 10
            breakdown["ev"] = 10
        elif ev < 0:
            score -= 15
            breakdown["ev"] = -15
        else:
            breakdown["ev"] = 0

        # Clamp score
        score = max(0, min(100, score))

        # Assign label
        if score >= 75:
            label = "WELL_ADAPTED"
        elif score >= 55:
            label = "MODERATELY_ADAPTED"
        elif score >= 40:
            label = "NEUTRAL"
        elif score >= 25:
            label = "POORLY_ADAPTED"
        else:
            label = "MISMATCHED"

        return {
            "score": score,
            "label": label,
            "breakdown": breakdown,
        }

    def record_pause_event(self, bot_id, strategy_id, metrics_at_pause, regime):
        """
        Record a pause event for later hindsight analysis.

        Args:
            bot_id (str): Bot identifier
            strategy_id (str): Strategy identifier
            metrics_at_pause (dict): Metrics snapshot at pause time
            regime (str): Regime at pause time
        """
        strategy_state = self.get_strategy_state(strategy_id)
        strategy_state["pause_events"].append(
            {
                "bot_id": bot_id,
                "timestamp": datetime.utcnow().isoformat(),
                "metrics_at_pause": dict(metrics_at_pause),
                "regime_at_pause": regime,
                "resolved": False,
                "outcome": None,
            }
        )

    def review_pause_events(self, current_metrics_by_strategy):
        """
        Review paused strategies for hindsight regret analysis.

        Args:
            current_metrics_by_strategy (dict): {strategy_id: metrics_dict}
        """
        total_events = 0
        regret_count = 0
        correct_count = 0

        for strategy_id, metrics in current_metrics_by_strategy.items():
            strategy_state = self.get_strategy_state(strategy_id)
            for event in strategy_state["pause_events"]:
                if event["resolved"]:
                    continue

                total_events += 1
                pause_metrics = event["metrics_at_pause"]

                pause_wr = pause_metrics.get("win_rate", 0)
                pause_pf = pause_metrics.get("profit_factor", 0)

                current_wr = metrics.get("win_rate", 0)
                current_pf = metrics.get("profit_factor", 0)

                wr_improvement = current_wr - pause_wr
                pf_improvement = current_pf - pause_pf

                # REGRET if improved significantly
                if wr_improvement > 3 and pf_improvement > 0.1:
                    event["resolved"] = True
                    event["outcome"] = "REGRET"
                    regret_count += 1
                # CORRECT if degraded
                elif current_wr < (pause_wr - 5) or current_pf < (pause_pf - 0.2):
                    event["resolved"] = True
                    event["outcome"] = "CORRECT"
                    correct_count += 1

        # Update calibration
        if total_events > 0:
            calibration = self.state["calibration"]
            calibration["pause_regret_rate"] = regret_count / total_events
            calibration["total_decisions"] += total_events
            calibration["correct_decisions"] += correct_count

    def enhanced_verdict(self, base_verdict, adaptation_score_result, strategy_id, bot_status):
        """
        Override base verdict based on learning engine insights.

        Args:
            base_verdict (str): Verdict from decision_engine
            adaptation_score_result (dict): Result from compute_adaptation_score
            strategy_id (str): Strategy identifier
            bot_status (str): Current bot status

        Returns:
            dict with:
                - verdict: str (possibly overridden)
                - reasons: list[str]
                - learning_insights: dict
        """
        reasons = []
        verdict = base_verdict
        learning_insights = {
            "adaptation_score": adaptation_score_result["score"],
            "adaptation_label": adaptation_score_result["label"],
        }

        score = adaptation_score_result["score"]
        strategy_state = self.get_strategy_state(strategy_id)
        calibration = self.state["calibration"]

        # Override 1: PAUSE but high adaptation → HOLD
        if base_verdict == "PAUSE" and score >= 80:
            verdict = "HOLD"
            reasons.append(
                f"Learning override: adaptation score {score} suggests drawdown is noise rather than edge decay"
            )
            learning_insights["override_1"] = "high_adaptation_override"

        # Override 2: PAUSE with history of regret → HOLD
        if base_verdict == "PAUSE" and score >= 70:
            regretted_pauses = sum(
                1 for event in strategy_state["pause_events"]
                if event.get("outcome") == "REGRET"
            )
            if regretted_pauses > 0:
                verdict = "HOLD"
                reasons.append(
                    f"Previously regretted pausing this strategy {regretted_pauses} times; adaptation {score} suggests holding"
                )
                learning_insights["override_2"] = "regret_history_override"

        # Override 3: PAUSE and high global regret rate → HOLD
        if (
            base_verdict == "PAUSE"
            and calibration["total_decisions"] > 10
            and calibration["pause_regret_rate"] > 0.50
            and score >= 70
        ):
            verdict = "HOLD"
            regret_pct = calibration["pause_regret_rate"] * 100
            reasons.append(
                f"System pause regret rate is {regret_pct:.1f}%; holding with adaptation score {score}"
            )
            learning_insights["override_3"] = "global_regret_override"

        # Warning 1: HOLD but poor adaptation
        if base_verdict == "HOLD" and score < 25:
            reasons.append(
                f"WATCH CLOSELY: adaptation score {score} indicates poor regime fit for {strategy_id}"
            )
            learning_insights["warning_1"] = "poor_adaptation_warning"

        learning_insights["final_verdict"] = verdict

        return {
            "verdict": verdict,
            "reasons": reasons,
            "learning_insights": learning_insights,
        }

    def update_regime_performance(self, strategy_id, regime, metrics):
        """
        Update regime-specific performance tracking for a strategy.

        Args:
            strategy_id (str): Strategy identifier
            regime (str): Current regime
            metrics (dict): Current metrics including win_rate, profit_factor, pnl
        """
        strategy_state = self.get_strategy_state(strategy_id)
        if regime not in strategy_state["regime_performance"]:
            strategy_state["regime_performance"][regime] = {
                "trades": 0,
                "wins": 0,
                "pnl": 0,
                "win_rate": 0,
                "profit_factor": 0,
            }

        regime_perf = strategy_state["regime_performance"][regime]

        # Update counts
        total_trades = metrics.get("total_trades", 0)
        trades_added = total_trades - regime_perf.get("trades", 0)

        if trades_added > 0:
            win_rate = metrics.get("win_rate", 0) / 100
            wins_added = int(trades_added * win_rate)

            regime_perf["trades"] = total_trades
            regime_perf["wins"] += wins_added
            regime_perf["win_rate"] = (regime_perf["wins"] / regime_perf["trades"] * 100) if regime_perf["trades"] > 0 else 0
            regime_perf["profit_factor"] = metrics.get("profit_factor", 0)
            regime_perf["pnl"] = metrics.get("pnl", 0)
