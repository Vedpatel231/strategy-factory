# Strategy Factory — Concrete Remediation Plan

**Context:** System lost 64% ($64,000) in 3 days. This plan prevents that from ever happening again.

---

## 1. Emergency Actions — Do Today

### 1.1 Disable Immediately

- **Disable auto-trading on Railway.** Delete the flag file or hit the API:
  ```bash
  # On Railway, via dashboard terminal or API call:
  rm -f data/alpaca_auto_trade.enabled
  ```
  This stops `AlpacaAutoTrader._loop()` from executing any further cycles.

- **Close all open Alpaca positions manually.** Log into Alpaca dashboard → Positions → Close All. Do not rely on the bot to do this — the bot is what caused the problem.

- **Do not re-enable auto-trading until Sections 2 and 3 are fully implemented and tested.**

### 1.2 Code Paths to Inspect First

| File | Lines | What to check |
|------|-------|---------------|
| `alpaca_auto_trader.py` | `_run_once()` (line 125-188) | No equity check before trading. No loss limit. No circuit breaker. |
| `alpaca_trader.py` | `execute_portfolio()` (line 83-273) | Confirm the aggregation fix is working. Verify the "close positions that dropped out" logic (line 225-247) doesn't mass-liquidate during config changes. |
| `portfolio_allocator.py` | `allocate_portfolio()` (line 86-90) | Equal-weight allocation ignores computed scores. |
| `learning_engine.py` | `_classify_regime()` (line 259) | Dead code: `1.5 <= cv <= 1.5` can never be true for non-exact values. |
| `config.py` | Pause thresholds (lines 22-31) | `PAUSE_WIN_RATE = 45.0` is extremely permissive — a 44% win rate bot still receives capital. |

### 1.3 Guardrails Required Before the Bot Runs Again

These are non-negotiable. The bot must not execute a single trade until all of these exist:

1. **Equity circuit breaker** — if account equity < 85% of recorded peak equity, halt all trading and close all positions.
2. **Daily loss limit** — if today's P&L < -3% of start-of-day equity, halt trading for the day.
3. **Position-level stop loss** — if any position is down 8% from entry, close it immediately.
4. **Order execution logging** — every order must be logged with timestamp, symbol, side, size, fill price, and reason. Currently orders are logged but not in a queryable format.
5. **Alert mechanism** — when any circuit breaker fires, write to a log file AND set a status that the dashboard can display prominently.

---

## 2. Root-Cause Fixes

### 2.1 Allocation Logic — Equal Weight is Reckless

**Plain English:** The system computes a quality score for each bot (considering win rate, profit factor, Sharpe ratio, drawdown, and adaptation score), then throws it away and gives every bot identical capital. A bot with 38% win rate and -25% drawdown gets the same $495 as one with 82% win rate and -2% drawdown.

**What needs to change:** Use the computed scores for allocation weighting. Higher-scoring strategies get more capital. Low-scoring strategies get less or none.

**Architecture change in `portfolio_allocator.py`:**

```python
# CURRENT (line 86-90):
equal_pct = 100.0 / len(eligible)
for e in eligible:
    e["final_pct"] = equal_pct

# REPLACE WITH: Score-weighted allocation with caps
total_score = sum(e["score"] for e in eligible)
for e in eligible:
    raw_pct = (e["score"] / total_score) * 100.0
    # Cap any single strategy at 8% of portfolio
    e["final_pct"] = min(raw_pct, 8.0)

# Re-normalize after capping
capped_total = sum(e["final_pct"] for e in eligible)
for e in eligible:
    e["final_pct"] = (e["final_pct"] / capped_total) * 100.0
    e["allocation_usd"] = round(capital * e["final_pct"] / 100, 2)
```

Also add a **minimum quality threshold** before a bot receives any capital:
```python
# Add after line 47 in portfolio_allocator.py:
if pf < 1.1 or win_rate < 45 or sharpe < 0.3:
    excluded.append({"bot_name": ev.get("bot_name"), "reason": f"Below quality threshold (WR={win_rate:.0f}%, PF={pf:.2f}, Sharpe={sharpe:.2f})"})
    continue
```

### 2.2 Signal Conflict Handling — Multiple Bots Same Coin

**Plain English:** 7 bots trade BTC simultaneously. Some might be bullish (momentum, trend), some bearish (mean reversion after a pump). The system allocates capital to all of them, effectively betting both sides of the same coin and paying fees twice.

**What needs to change:** Add a **signal consensus layer** between portfolio allocation and trade execution. Per coin, if bots disagree, reduce total allocation to that coin proportionally to disagreement.

**New logic to add in `alpaca_trader.py` before the execution loop:**

```python
# After aggregating target_by_symbol, check for signal conflicts
# A "conflict" is when bots on the same symbol have opposing verdicts
# This requires passing verdict info through to the trader

# In the allocation phase, add conflict detection:
for sym, target in target_by_symbol.items():
    bot_names = target["bot_names"]
    # Count bots with HOLD vs those with implied bearish signals
    # If >40% of bots on this symbol scored below median, reduce allocation
    if target.get("conflict_ratio", 0) > 0.4:
        target["target_usd"] *= 0.5  # halve allocation on conflicted symbols
        logger.warning(f"Signal conflict on {sym}: reducing allocation 50%")
```

### 2.3 Portfolio-Level Risk — No Correlation Awareness

**Plain English:** Crypto coins are highly correlated (BTC drops, everything drops). The system treats 28 coins as 28 independent bets, but in reality they often move together. During a broad selloff, all positions lose simultaneously.

**What needs to change:** Add a **portfolio correlation cap**. Track rolling correlation between positions and reduce exposure when average correlation exceeds a threshold.

**Implementation location:** New file `risk_manager.py` (see Section 3 for full implementation).

**Key logic:**
```python
# If average pairwise correlation of held positions > 0.7:
#   - Reduce total portfolio exposure to 50% (hold 50% in cash)
# If > 0.85:
#   - Reduce to 25% exposure
# This prevents the "everything drops at once" scenario
```

### 2.4 Rebalance Logic — 30 Minutes is Destructive

**Plain English:** Every 30 minutes, the system checks if positions have drifted >15% from target and trades to fix it. In volatile crypto markets, 15% drift happens constantly. This creates excessive trading: buy, sell, buy, sell — each time paying spread and fees. During the bug period, this churn amplified losses by repeatedly selling at lows and buying back higher.

**What needs to change:**

1. **Increase rebalance interval** from 30 minutes to **24 hours** (or 12 hours minimum).
2. **Increase drift threshold** from 15% to **25%** — only trade when positions have truly drifted.
3. **Add trade cost estimation** — if the estimated cost of rebalancing exceeds the expected benefit, skip it.

**Changes:**

```python
# In alpaca_auto_trader.py:
DEFAULT_INTERVAL_MIN = int(os.environ.get("ALPACA_AUTO_TRADE_INTERVAL_MIN", "1440"))  # 24 hours

# In alpaca_trader.py:
REBALANCE_THRESHOLD_PCT = 25.0  # was 15.0

# Add cost check before executing:
estimated_cost = order_usd * 0.003  # ~0.3% round-trip cost estimate
expected_benefit = abs(pct_diff - REBALANCE_THRESHOLD_PCT) / 100 * dollar_alloc * 0.01
if estimated_cost > expected_benefit:
    results["skipped"].append({"bot": label, "pair": sym, "reason": "Trade cost exceeds benefit"})
    continue
```

### 2.5 Regime Detection — Dead Code and Weak Classification

**Plain English:** The market regime detector has a bug where the "choppy" market condition can never be detected (`1.5 <= cv <= 1.5` is essentially `cv == 1.5`). It also detects regime from aggregated bot PnL rather than actual market price data, so it conflates "are my bots making money" with "what is the market doing."

**What needs to change:**

1. **Fix the choppy condition:**
```python
# In learning_engine.py line 259, REPLACE:
if 1.5 <= cv <= 1.5 and abs(autocorr) < 0.3:

# WITH:
if 1.0 <= cv <= 3.0 and abs(autocorr) < 0.2:
```

2. **Use market price data for regime detection, not bot PnL.** The regime detector should ingest BTC/ETH price history (available from Alpaca API) rather than equity curves. Bot PnL reflects strategy quality, not market conditions.

3. **Add regime persistence requirement.** Don't switch regime on a single reading. Require 3 consecutive detections of the same regime before officially transitioning. This prevents whipsawing.

```python
# Add to detect_regime():
if len(self.state["regime_history"]) >= 3:
    last_3 = [r["regime"] for r in self.state["regime_history"][-3:]]
    if len(set(last_3)) > 1:
        # Not stable yet — keep previous regime
        regime = self.state["current_regime"]
```

### 2.6 Learning Engine — Produces No Useful Signal

**Plain English:** The learning engine starts every bot at score 50 and adjusts based on 5 components. But 4 of the 5 components require `regime_trades >= 10` of history in the current regime, which doesn't exist for a new system. So virtually all bots score 50-65, providing no differentiation. The enhanced_verdict overrides also bias heavily toward never pausing anything.

**What needs to change:**

1. **Lower the regime_trades threshold** from 10 to 3 for initial signal, with confidence scaling:
```python
# Replace the hard threshold:
regime_trades = regime_perf.get("trades", 0)
if regime_trades >= 3:  # was 10
    confidence = min(1.0, regime_trades / 10)  # scale up as data accumulates
    # Apply adjustments multiplied by confidence
    if win_rate > 55:
        score += int(15 * confidence)
```

2. **Remove the triple-override bias** that prevents pausing. Currently overrides 1, 2, and 3 in `enhanced_verdict()` all convert PAUSE → HOLD with low thresholds (score >= 50 is enough with override 3). This means almost nothing ever gets paused.

```python
# Make overrides more conservative:
# Override 1: require score >= 80 (was 70)
if base_verdict == "PAUSE" and score >= 80:
    verdict = "HOLD"

# Override 3: require score >= 70 AND regret rate > 50% (was score >= 50 AND > 30%)
if base_verdict == "PAUSE" and calibration["total_decisions"] > 10 and calibration["pause_regret_rate"] > 0.50 and score >= 70:
    verdict = "HOLD"
```

---

## 3. Risk Controls to Implement

### 3.1 Max Daily Loss Stop

**Why it matters:** Without this, a bad day compounds into a catastrophic day. The auto-trader runs multiple cycles per day and can lose money on every single one.

**What it should do:** Track start-of-day equity. If current equity drops below (start_of_day * 0.97), immediately halt all trading for the remainder of the day. Close any positions that are individually down more than 5%.

**Pseudocode:**
```python
class DailyLossGuard:
    def __init__(self, max_daily_loss_pct=3.0):
        self.max_loss_pct = max_daily_loss_pct
        self.start_of_day_equity = None
        self.last_reset_date = None
        self.halted = False

    def check(self, current_equity):
        today = datetime.utcnow().date()
        if self.last_reset_date != today:
            self.start_of_day_equity = current_equity
            self.last_reset_date = today
            self.halted = False

        if self.start_of_day_equity is None:
            return True  # safe to trade

        loss_pct = (self.start_of_day_equity - current_equity) / self.start_of_day_equity * 100
        if loss_pct >= self.max_loss_pct:
            self.halted = True
            logger.critical(f"DAILY LOSS LIMIT HIT: -{loss_pct:.2f}% (limit: {self.max_loss_pct}%)")
            return False  # DO NOT TRADE
        return True
```

**Where to implement:** Create `risk_manager.py`. Call `daily_loss_guard.check(equity)` at the top of `AlpacaAutoTrader._run_once()` and `AlpacaTrader.execute_portfolio()`. If it returns False, abort immediately.

### 3.2 Max Drawdown Stop (Equity Circuit Breaker)

**Why it matters:** This is the last line of defense. If the account drops X% from its all-time high, something is fundamentally broken and the system must stop completely until a human reviews it.

**What it should do:** Track peak equity (persisted to disk). If current equity < 85% of peak, close all positions, disable auto-trading, and write an alert.

**Pseudocode:**
```python
class DrawdownCircuitBreaker:
    PEAK_FILE = os.path.join(config.DATA_DIR, "peak_equity.json")

    def __init__(self, max_drawdown_pct=15.0):
        self.max_dd_pct = max_drawdown_pct
        self.peak_equity = self._load_peak()

    def _load_peak(self):
        if os.path.exists(self.PEAK_FILE):
            with open(self.PEAK_FILE) as f:
                return json.load(f).get("peak", 0)
        return 0

    def _save_peak(self):
        os.makedirs(os.path.dirname(self.PEAK_FILE), exist_ok=True)
        with open(self.PEAK_FILE, "w") as f:
            json.dump({"peak": self.peak_equity, "updated": datetime.utcnow().isoformat()}, f)

    def check(self, current_equity):
        # Update peak
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            self._save_peak()

        if self.peak_equity <= 0:
            return True

        drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity * 100
        if drawdown_pct >= self.max_dd_pct:
            logger.critical(f"CIRCUIT BREAKER: Drawdown {drawdown_pct:.1f}% exceeds {self.max_dd_pct}% limit")
            logger.critical(f"Peak: ${self.peak_equity:.2f}, Current: ${current_equity:.2f}")
            self._emergency_shutdown()
            return False
        return True

    def _emergency_shutdown(self):
        # 1. Disable auto-trading
        AlpacaAutoTrader.set_enabled(False)
        # 2. Close all positions
        from alpaca_client import AlpacaPaperClient
        client = AlpacaPaperClient()
        positions = client.get_positions()
        for pos in positions:
            try:
                client.close_position(pos["symbol"])
            except Exception as e:
                logger.error(f"Failed to close {pos['symbol']}: {e}")
        # 3. Write alert file
        alert = {
            "type": "CIRCUIT_BREAKER",
            "timestamp": datetime.utcnow().isoformat(),
            "message": "Max drawdown exceeded. All trading halted. All positions closed.",
            "peak_equity": self.peak_equity,
        }
        with open(os.path.join(config.DATA_DIR, "ALERT_CIRCUIT_BREAKER.json"), "w") as f:
            json.dump(alert, f, indent=2)
```

**Where to implement:** `risk_manager.py`. Called at the very start of `_run_once()` in `alpaca_auto_trader.py`, before any other logic.

### 3.3 Kill Switch

**Why it matters:** When something goes wrong, a human needs a single action to stop everything. Currently "disable" only prevents the next cycle — it doesn't close existing positions that are bleeding money.

**What it should do:** One API endpoint and one dashboard button that: (a) disables auto-trading, (b) cancels all open orders, (c) closes all positions, (d) logs the emergency shutdown.

**Pseudocode:**
```python
# In dashboard_server.py, add endpoint:
@app.route("/api/emergency/kill", methods=["POST"])
@require_auth
def emergency_kill():
    """Nuclear option: close everything, disable everything."""
    results = {"timestamp": datetime.utcnow().isoformat(), "actions": []}

    # 1. Disable auto-trading
    AlpacaAutoTrader.set_enabled(False)
    AlpacaAutoTrader.get().stop()
    results["actions"].append("Auto-trading disabled")

    # 2. Close all positions
    try:
        from alpaca_client import AlpacaPaperClient
        client = AlpacaPaperClient()
        positions = client.get_positions()
        for pos in positions:
            client.close_position(pos["symbol"])
            results["actions"].append(f"Closed {pos['symbol']}")
    except Exception as e:
        results["actions"].append(f"Error closing positions: {e}")

    # 3. Write kill log
    with open(os.path.join(DATA_DIR, "kill_switch.log"), "a") as f:
        f.write(json.dumps(results, default=str) + "\n")

    return jsonify(results)
```

**Where to implement:** Add to `dashboard_server.py`. Add a prominent red "EMERGENCY STOP" button to the Alpaca page of the dashboard.

### 3.4 Per-Position Stop Loss

**Why it matters:** Without position-level stops, a single coin can crash 30%+ and the system does nothing until the next rebalance cycle (potentially hours away).

**What it should do:** Before executing any new trades, scan all existing positions. If any position is down more than 8% from its cost basis, close it immediately regardless of what the portfolio allocator says.

**Pseudocode:**
```python
class PositionStopLoss:
    def __init__(self, max_loss_pct=8.0):
        self.max_loss_pct = max_loss_pct

    def check_and_close(self, client):
        """Check all positions and close any that hit stop loss."""
        positions = client.get_positions()
        closed = []
        for pos in positions:
            cost = pos.get("cost_basis", 0)
            current = pos.get("market_value", 0)
            if cost <= 0:
                continue
            loss_pct = (cost - current) / cost * 100
            if loss_pct >= self.max_loss_pct:
                logger.warning(f"STOP LOSS: {pos['symbol']} down {loss_pct:.1f}% — closing")
                try:
                    client.close_position(pos["symbol"])
                    closed.append({"symbol": pos["symbol"], "loss_pct": loss_pct})
                except Exception as e:
                    logger.error(f"Failed stop-loss close on {pos['symbol']}: {e}")
        return closed
```

**Where to implement:** Add to `risk_manager.py`. Call at the start of every `execute_portfolio()` call in `alpaca_trader.py`, BEFORE processing new allocations.

### 3.5 Portfolio Exposure Caps

**Why it matters:** Prevents over-concentration in a single coin or strategy type that could cause outsized losses if that specific market segment crashes.

**What it should do:**
- No single coin > 12% of portfolio value
- No single strategy type > 30% of portfolio value
- Total crypto exposure capped at 90% (always hold 10% cash reserve)

**Pseudocode:**
```python
class ExposureLimits:
    MAX_COIN_PCT = 12.0
    MAX_STRATEGY_TYPE_PCT = 30.0
    MAX_TOTAL_EXPOSURE_PCT = 90.0  # 10% always in cash

    def apply(self, target_by_symbol, total_equity, allocations_metadata):
        """Reduce allocations that exceed caps."""
        # Cap 1: Per-coin limit
        for sym, target in target_by_symbol.items():
            max_usd = total_equity * self.MAX_COIN_PCT / 100
            if target["target_usd"] > max_usd:
                logger.info(f"Capping {sym} from ${target['target_usd']:.0f} to ${max_usd:.0f}")
                target["target_usd"] = max_usd

        # Cap 2: Total exposure
        total_target = sum(t["target_usd"] for t in target_by_symbol.values())
        max_total = total_equity * self.MAX_TOTAL_EXPOSURE_PCT / 100
        if total_target > max_total:
            scale = max_total / total_target
            for target in target_by_symbol.values():
                target["target_usd"] *= scale
            logger.info(f"Total exposure capped: scaled by {scale:.2f}")

        return target_by_symbol
```

**Where to implement:** Add to `risk_manager.py`. Call in `alpaca_trader.py` after building `target_by_symbol` but before the execution loop.

### 3.6 Strategy Disable Rules

**Why it matters:** A strategy that consistently loses money should be automatically removed from the portfolio, not just scored lower.

**What it should do:** If a strategy has negative P&L for 7 consecutive days, OR its rolling 14-day Sharpe is below -0.5, OR it has triggered 3 stop losses in a week, disable it for 30 days.

**Pseudocode:**
```python
class StrategyDisabler:
    DISABLE_FILE = os.path.join(config.DATA_DIR, "disabled_strategies.json")

    def __init__(self):
        self.disabled = self._load()

    def should_trade(self, bot_name):
        """Return False if this strategy is currently disabled."""
        entry = self.disabled.get(bot_name)
        if not entry:
            return True
        disabled_until = datetime.fromisoformat(entry["disabled_until"])
        if datetime.utcnow() > disabled_until:
            del self.disabled[bot_name]
            self._save()
            return True
        return False

    def check_and_disable(self, bot_name, metrics):
        """Disable strategy if it meets failure criteria."""
        consecutive_loss_days = metrics.get("consecutive_loss_days", 0)
        rolling_sharpe = metrics.get("rolling_14d_sharpe", 0)
        stop_losses_this_week = metrics.get("stop_losses_7d", 0)

        reason = None
        if consecutive_loss_days >= 7:
            reason = f"7 consecutive losing days"
        elif rolling_sharpe < -0.5:
            reason = f"14-day Sharpe {rolling_sharpe:.2f} below -0.5"
        elif stop_losses_this_week >= 3:
            reason = f"{stop_losses_this_week} stop losses in 7 days"

        if reason:
            disable_until = (datetime.utcnow() + timedelta(days=30)).isoformat()
            self.disabled[bot_name] = {"reason": reason, "disabled_until": disable_until}
            self._save()
            logger.warning(f"DISABLED {bot_name}: {reason} — disabled until {disable_until}")
            return True
        return False
```

**Where to implement:** `risk_manager.py`. Check in `portfolio_allocator.py` before including a bot in eligible list.

### 3.7 Cooldown Rules

**Why it matters:** After a losing period, immediately re-entering at full size often compounds losses. Markets that just caused a loss are likely still adverse.

**What it should do:** After the daily loss limit is hit, the next day's maximum exposure is reduced to 50%. After a circuit breaker event, the system requires manual re-enablement and starts at 25% exposure for the first week.

**Pseudocode:**
```python
class CooldownManager:
    def get_exposure_multiplier(self):
        """Return 0.0-1.0 multiplier for current exposure level."""
        # Check if we hit daily loss yesterday
        if self._daily_loss_hit_yesterday():
            return 0.5  # 50% exposure next day

        # Check if circuit breaker fired recently
        days_since_cb = self._days_since_circuit_breaker()
        if days_since_cb is not None and days_since_cb < 7:
            return 0.25  # 25% exposure for first week after CB

        return 1.0  # Normal exposure
```

**Where to implement:** `risk_manager.py`. Apply the multiplier to all allocations in `alpaca_trader.py` before execution.

### 3.8 Trade Frequency Limits

**Why it matters:** Prevents runaway trading from bugs or extreme volatility. Caps the number of orders per day to a sane level.

**What it should do:** Maximum 50 orders per day (across all symbols). Maximum 5 orders per symbol per day. If limits are hit, halt trading until next day.

**Pseudocode:**
```python
class TradeFrequencyLimiter:
    MAX_DAILY_ORDERS = 50
    MAX_PER_SYMBOL_DAILY = 5

    def __init__(self):
        self.daily_orders = {}  # {date: {symbol: count}}

    def can_trade(self, symbol):
        today = datetime.utcnow().date().isoformat()
        if today not in self.daily_orders:
            self.daily_orders = {today: {}}

        day_data = self.daily_orders[today]
        total_today = sum(day_data.values())

        if total_today >= self.MAX_DAILY_ORDERS:
            logger.warning(f"FREQUENCY LIMIT: {total_today} orders today (max {self.MAX_DAILY_ORDERS})")
            return False

        symbol_count = day_data.get(symbol, 0)
        if symbol_count >= self.MAX_PER_SYMBOL_DAILY:
            logger.warning(f"FREQUENCY LIMIT: {symbol} traded {symbol_count}x today")
            return False

        return True

    def record_trade(self, symbol):
        today = datetime.utcnow().date().isoformat()
        if today not in self.daily_orders:
            self.daily_orders = {today: {}}
        self.daily_orders[today][symbol] = self.daily_orders[today].get(symbol, 0) + 1
```

**Where to implement:** `risk_manager.py`. Check before each `client.submit_order()` call in `alpaca_trader.py`.

---

## 4. Validation Plan

### 4.1 Testing Each Fix in Paper Trading

| Fix | Test Method | Duration | Pass Criteria |
|-----|------------|----------|---------------|
| Equity circuit breaker | Artificially set peak to current equity + 20%. Run normally. Verify it fires when equity drops 15% from that fake peak. | 1 day | System halts, positions close, alert file created |
| Daily loss limit | Set limit to 0.5% for testing. Run during volatile hours. Verify it fires and halts. | 1 day | Trading stops within 1 cycle of limit breach |
| Position stop loss | Open a small position in a volatile coin. Set stop to 2% for testing. Wait for it to trigger. | 2 days | Position auto-closes when loss exceeds threshold |
| Score-weighted allocation | Run dry-run with new allocator. Compare to equal-weight. Verify high-score bots get more capital. | Immediate | Top-quartile bots get 2-3x capital vs bottom quartile |
| Exposure caps | Run dry-run with 12% per-coin cap. Verify BTC allocation (7 bots) doesn't exceed 12%. | Immediate | No coin exceeds cap in dry-run output |
| Rebalance interval | Set to 24h. Monitor that trades only happen once per day. | 3 days | Exactly 1 rebalance cycle per 24h period |
| Regime fix | Log regime classifications. Verify "choppy" can now be detected with appropriate test data. | 1 week | At least one "choppy" classification in varied markets |
| Strategy disable | Artificially inject 7 consecutive loss days for one bot. Verify it gets disabled. | 1 day | Bot excluded from next allocation cycle |

### 4.2 Metrics to Track During Paper Testing

Track these daily for the 90-day paper test period:

- **Portfolio equity** (end of day)
- **Daily P&L** ($ and %)
- **Max intraday drawdown**
- **Number of trades executed**
- **Total trading costs** (estimated from spread)
- **Number of circuit breaker events**
- **Number of stop-loss closures**
- **Number of strategies disabled**
- **Win rate** (per strategy and portfolio-level)
- **Sharpe ratio** (rolling 30-day)
- **Correlation** (average pairwise between held positions)

### 4.3 Pass/Fail Thresholds

The system PASSES the 90-day paper test if ALL of these are met:

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Maximum drawdown | < 15% | Must survive worst period without catastrophic loss |
| Sharpe ratio (90-day) | > 0.5 | Returns must compensate for risk taken |
| Win rate (portfolio-level) | > 48% | Must win more often than lose on net |
| Trading costs as % of gross profit | < 25% | Not churning away profits |
| Circuit breaker events | 0 | Should never hit the 15% drawdown limit |
| Daily loss limit events | < 5 total | Occasional bad days acceptable, frequent ones are not |
| Consecutive losing days | < 10 | Extended losing streaks indicate broken logic |
| System uptime | > 95% | Reliable execution without crashes |

The system FAILS and requires further work if ANY of these occur:

- Drawdown exceeds 15% at any point
- Sharpe ratio negative for any rolling 30-day window
- More than 3 circuit breaker events
- Any single day loses more than 5% (indicating daily loss limit didn't fire)
- Trading costs exceed gross profits (net negative after fees)

### 4.4 Proof That the System Is Improving

Compare these metrics between weeks 1-4 and weeks 5-12:

- Drawdown should be decreasing (learning engine adapting)
- Win rate should be stable or increasing
- Number of disabled strategies should plateau (bad ones get weeded out)
- Average adaptation score should diverge (good bots score higher, bad ones lower — not clustered)
- Correlation between positions should be managed (below 0.7 average)

---

## 5. Simplified Restart Plan

### 5.1 Reduce From 202 Bots to a Controlled Set

**Keep 24 bots maximum.** Here's how to select them:

- **8 coins only:** BTC, ETH, SOL, XRP, LINK, AVAX, DOGE, ADA (highest liquidity on Alpaca, lowest spreads)
- **3 strategy types only:** Grid (highest win rate), Mean Reversion (best risk-adjusted), Momentum (best for trends)
- **8 coins x 3 types = 24 bots**

Remove all other strategy types (scalping, trend_following, breakout, swing) until the core 24 prove profitable.

**Implementation:**
```python
# In seed_data.py or a new config, define the active set:
ACTIVE_COINS = ["BTC", "ETH", "SOL", "XRP", "LINK", "AVAX", "DOGE", "ADA"]
ACTIVE_STRATEGIES = ["grid", "mean_reversion", "momentum"]

# In portfolio_allocator.py, filter eligible bots:
if ev.get("pair", "").split("/")[0] not in ACTIVE_COINS:
    excluded.append({"bot_name": ev.get("bot_name"), "reason": "Coin not in active set"})
    continue
if ev.get("strategy_type", "") not in ACTIVE_STRATEGIES:
    excluded.append({"bot_name": ev.get("bot_name"), "reason": "Strategy type not in active set"})
    continue
```

### 5.2 Rebalance Frequency

- **Rebalance once every 24 hours**, at a fixed time (10:00 AM Eastern, matching `config.SCHEDULE_HOUR`).
- **Drift threshold: 25%** — only trade if a position has drifted more than 25% from target.
- **Minimum trade size: $50** — ignore tiny rebalancing needs that cost more in fees than they correct.

### 5.3 When a Strategy Should Receive Capital

A strategy must meet ALL of these to receive any allocation:

| Requirement | Threshold | Rationale |
|-------------|-----------|-----------|
| Minimum trades | > 30 | Enough data for statistical significance |
| Win rate | > 48% | Must win more often than lose |
| Profit factor | > 1.15 | Must make more on wins than lose on losses |
| Sharpe ratio | > 0.3 | Positive risk-adjusted return |
| Max drawdown | < -15% | Not too volatile |
| Consecutive losses | < 5 current | Not in an active losing streak |
| Adaptation score | > 45 | Not mismatched to current regime |
| Not disabled | True | Not in cooldown period |

If ANY threshold is violated, the strategy receives $0 and goes to the excluded list.

### 5.4 Scaling Plan

| Month | Max bots | Max coins | Max total capital | Rebalance interval |
|-------|----------|-----------|-------------------|--------------------|
| 1-3 (paper) | 24 | 8 | $100,000 paper | 24 hours |
| 4 (live start) | 24 | 8 | $1,000 real | 24 hours |
| 5 | 24 | 8 | $2,500 real | 24 hours |
| 6 | 36 | 12 | $5,000 real | 12 hours |
| 7+ | Up to 50 | Up to 16 | Scale 2x/month if profitable | 12 hours |

Never exceed 50 bots until the system has 6+ months of profitable live history.

---

## 6. Live-Readiness Checklist

Before risking even $100 of real money, ALL of the following must be true:

### Risk Controls (all must be implemented AND tested)

- [ ] Equity circuit breaker implemented and tested (fires at -15% from peak)
- [ ] Daily loss limit implemented and tested (halts at -3% daily)
- [ ] Per-position stop loss implemented and tested (closes at -8%)
- [ ] Kill switch endpoint exists and tested (closes all positions in < 60 seconds)
- [ ] Portfolio exposure caps active (no coin > 12%, total < 90%)
- [ ] Trade frequency limiter active (< 50 orders/day)
- [ ] Strategy disable logic active (auto-disables after 7 losing days)
- [ ] Cooldown multiplier active after loss events

### Performance (all must be demonstrated in paper trading)

- [ ] 90 consecutive days of paper trading completed
- [ ] No manual interventions or code changes during final 30 days
- [ ] Maximum drawdown during test: < 15%
- [ ] 90-day Sharpe ratio: > 0.5
- [ ] No zero-day circuit breaker events in final 30 days
- [ ] Positive net P&L after estimated trading costs
- [ ] All 24 active bots have > 50 trades of history

### System Reliability

- [ ] Zero critical bugs in final 30 days
- [ ] Auto-trader ran on schedule for 30 consecutive days without crash
- [ ] Dashboard correctly displays all risk control statuses
- [ ] Alert mechanism works (fires email/notification on circuit breaker)
- [ ] All orders are logged and auditable

### Verification

- [ ] Paper trading P&L matches Alpaca account statement (< 1% discrepancy)
- [ ] Regime detector produces varied classifications (not stuck on one)
- [ ] Learning engine scores show clear differentiation (std dev > 15)
- [ ] Strategy disabler has correctly identified at least 3 bad strategies

### Human Process

- [ ] Written runbook for "what to do when circuit breaker fires"
- [ ] Daily monitoring habit established (check dashboard at least once per day)
- [ ] Maximum real capital defined and agreed ($1,000 initial)
- [ ] Scaling rules defined (only increase capital after profitable month)
- [ ] Stop-loss for the entire project defined (if account drops 20% from starting capital, shut down completely and review)

---

## Implementation Priority Order

If you can only do one thing per day, do them in this order:

| Day | Task | File(s) |
|-----|------|---------|
| 1 | Create `risk_manager.py` with DrawdownCircuitBreaker + DailyLossGuard | New file |
| 2 | Wire circuit breaker into `alpaca_auto_trader.py` `_run_once()` | `alpaca_auto_trader.py` |
| 3 | Add PositionStopLoss, call it at top of `execute_portfolio()` | `risk_manager.py`, `alpaca_trader.py` |
| 4 | Add kill switch endpoint to dashboard server + UI button | `dashboard_server.py`, `generate_dashboard.py` |
| 5 | Fix regime detection dead code | `learning_engine.py` line 259 |
| 6 | Replace equal-weight with score-weighted allocation + quality threshold | `portfolio_allocator.py` |
| 7 | Add ExposureLimits + TradeFrequencyLimiter | `risk_manager.py`, `alpaca_trader.py` |
| 8 | Increase rebalance interval to 24h + increase threshold to 25% | `alpaca_auto_trader.py`, `alpaca_trader.py` |
| 9 | Reduce active bots to 24 (8 coins x 3 types) | `portfolio_allocator.py` or new config |
| 10 | Add strategy disable + cooldown logic | `risk_manager.py`, `portfolio_allocator.py` |
| 11 | Tighten learning engine overrides | `learning_engine.py` |
| 12 | Add dashboard alert display for risk events | `generate_dashboard.py` |
| 13 | Begin 90-day paper test | Monitor daily |

---

## Summary

The core message is simple: **risk controls first, performance second.** A system that makes 5% per month but can't lose more than 15% total is infinitely better than one that might make 20% but can also lose 64% in three days.

Build the guardrails. Prove they work. Then — slowly, carefully — turn the system back on.
