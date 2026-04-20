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
                "regime_performance": {
                    "trending_up": {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0, "profit_factor": 0},
                    "trending_down": {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0, "profit_factor": 0},
                    "mean_reverting": {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0, "profit_factor": 0},
                    "high_volatility": {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0, "profit_factor": 0},
                    "low_volatility": {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0, "profit_factor": 0},
                    "choppy": {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0, "profit_factor": 0},
                    "unknown": {"trades": 0, "wins": 0, "pnl": 0, "win_rate": 0, "profit_factor": 0},
                },
                "adaptation_history": [],
                "pause_events": [],
            }
        return self.state["strategies"][strategy_id]

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
            recent_wins = sum(1 for t in recent_trades[-10:] if t.get("win", False))
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
            first_half_wins = sum(1 for t in recent_trades[:mid] if t.get("win", False))
            second_half_wins = sum(1 for t in recent_trades[mid:] if t.get("win", False))

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
