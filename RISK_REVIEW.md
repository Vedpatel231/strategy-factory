# Strategy Factory — Independent Risk Review

**Reviewer perspective:** Skeptical real-money user and risk manager evaluating whether to allocate personal capital.

**System under review:** Strategy Factory crypto trading dashboard with 202 bots across 28 coins, 7 strategy types, connected to Alpaca paper trading.

**Key fact:** $100,000 paper account dropped to ~$36,000 in approximately 3 days — a 64% drawdown.

---

## 1. Honest First Reaction

A 64% drawdown in 3 days is not a "bad start." It is a catastrophic system failure. For context, the S&P 500's worst single-day drop in modern history was about 20% (Black Monday, 1987). This system lost triple that in three days, in a paper account, during what was not an unprecedented market crash.

My immediate classification: **this is a broken execution and risk system, not merely an immature trading system.** An immature system might lose 5-15% while finding its footing. A badly designed system might lose 20-30% over weeks. Losing 64% in 3 days means either the system is actively destroying capital through a mechanical bug, or it has zero risk controls and is amplifying bad decisions with no circuit breaker.

The confirmed root cause — a code bug where `target_by_symbol` was overwriting allocations instead of aggregating them — partially explains the loss. With 7 strategies per coin, each rebalance cycle was selling down positions to 1/7th of intended size, realizing losses on each cycle. But the fact that such a bug could silently destroy 64% of capital with no alarm, no halt, and no human notification is itself the deeper problem.

If this were real money, $64,000 would be gone permanently. There is no undo button.

---

## 2. Most Likely Failure Points

After reading every core file in the system, here are the specific design weaknesses I found:

**No risk controls whatsoever.** The system has zero portfolio-level risk management. There is no max drawdown stop, no daily loss limit, no position size cap, no volatility filter, no kill switch. The `alpaca_auto_trader.py` runs every 30 minutes and will keep executing regardless of how much money has been lost. The only thing that can stop it is manually deleting a flag file on the server.

**Equal-weight allocation is naive and dangerous.** The `portfolio_allocator.py` computes composite scores for each strategy but then ignores them entirely for allocation purposes. Every eligible bot gets `100% / N` of capital. A bot with a 38% win rate and -25% max drawdown gets the same allocation as one with 82% win rate and -2% drawdown. The score is computed, displayed, and then discarded. This is not diversification — it is indifference.

**202 bots is not diversification — it is dilution.** With 28 coins and 7 strategy types, you have ~7 bots per coin all placing effectively the same directional bet on the same asset. When BTC drops, all 7 BTC bots lose money simultaneously. The system treats "BTC Scalper," "BTC Momentum," and "BTC Trend" as if they are diversified positions, but they are all long BTC. The Herfindahl diversification score the system computes is meaningless because it measures allocation weight spread, not actual return correlation.

**Conflicting signals are averaged away, not resolved.** If 4 out of 7 BTC bots say "buy" and 3 say "sell," the system allocates capital to all 7. The buys and sells partially cancel, but you still pay fees and slippage on every order. There is no signal consensus mechanism.

**The rebalance logic is destructive during regime changes.** Every 30 minutes, the auto-trader compares current positions to target allocations. If any position drifts more than 15% from target, it trades. In a volatile crypto market, this threshold is hit constantly, generating excessive trading that erodes capital through fees and slippage.

**The learning engine produces almost no useful signal.** Reading `learning_engine.py`, the adaptation score starts at 50 and has 5 components — but most components return 0 because they require `regime_trades >= 10` in the current regime, and the system hasn't accumulated enough regime-specific trade history. In practice, virtually every bot scores between 50-65. The "learning" is cosmetic.

**Regime detection is broken.** In `_classify_regime()`, line 259: `if 1.5 <= cv <= 1.5` — this condition can never be true (a number cannot be simultaneously >= 1.5 and <= 1.5 unless it is exactly 1.5). The "choppy" regime is effectively dead code. The regime detector also uses aggregate PnL across all strategies as a proxy for market conditions, which conflates strategy performance with market regime.

**The decision engine can be overridden too easily.** The `enhanced_verdict()` method in the learning engine has three separate override paths that can convert a PAUSE verdict to HOLD. If a strategy's adaptation score is >= 70 (override 1), or >= 55 with any prior regret (override 2), or >= 50 with high global regret rate (override 3), it won't be paused. Given that scores cluster around 50-65, override 3 is almost always active once the system has made a few decisions. The system is biased toward never pausing anything.

**All performance data is simulated.** The `seed_data.py` file generates 30 days of fake performance history using random numbers seeded from strategy names. The win rates, profit factors, and Sharpe ratios that the dashboard displays are not from real trading — they are from `random.gauss()` calls. Every decision the system makes is based on fictional data.

**No slippage, spread, or fee modeling.** The `_estimate_monthly_return()` function in `portfolio_allocator.py` calculates expected returns without accounting for trading costs. In crypto, spreads on smaller altcoins can be 0.5-2%, and with the system trading every 30 minutes across 28 coins, transaction costs alone could be 10-30% per month.

---

## 3. Real-Money User Perspective

If I deposited $100,000 of real money and saw it become $36,000 in three days, here is what I would think:

This product is not ready for real money. Period. The fact that the loss was caused by a code bug — not a market event — makes it worse, not better. It means the system was never tested end-to-end with real execution logic before being connected to a broker. A single Python dict overwrite destroyed $64,000.

Before I would trust any system with real capital, the dashboard would need to prove the following — and it currently proves none of these:

The system needs a track record page showing at minimum 90 days of uninterrupted paper trading with full position-level detail, verified against broker statements. Not simulated data from `seed_data.py`. Real fills, real slippage, real P&L.

The system needs a risk controls page showing active circuit breakers: max drawdown halt, daily loss limit, position concentration limits, and the current status of each. The dashboard currently has no such page.

The system needs a trade cost analysis showing cumulative fees, slippage estimates, and their impact on returns. Currently absent.

The system needs drawdown visualization — not just current drawdown, but peak-to-trough history over time, worst drawdown duration, and recovery time. The Performance page shows win rates and profit factors from seed data, not actual drawdown curves.

The system needs a "last time the system was wrong" section. How often does the learning engine override a PAUSE and the strategy keeps losing? What is the actual regret rate? Currently the calibration object tracks this but it is not surfaced prominently.

---

## 4. What Is Missing

These are the most dangerous missing features, ranked by how likely each is to cause real capital loss:

**Drawdown-based shutdown (critical).** If the portfolio drops X% from peak equity, all trading must halt automatically. This is non-negotiable. Currently the system will trade itself to zero without flinching.

**Daily loss limit (critical).** If losses exceed $Y in a single day, halt all new orders. The auto-trader runs every 30 minutes and will compound losses across 48 cycles per day.

**Kill switch (critical).** A single button that immediately closes all positions and disables auto-trading. The current "disable" toggle only prevents the next cycle — it does not close existing positions.

**Position concentration limits (high).** No single coin should represent more than X% of portfolio. Currently BTC could be 7/202 of allocation = 3.5%, which seems fine, but after the redistribution factor for unsupported coins it could be higher.

**Stop-loss framework (high).** No individual position or strategy has a stop-loss. The system relies entirely on the 30-minute rebalance cycle to adjust positions. In a flash crash, positions can lose 20%+ before the next cycle runs.

**Strategy disable logic (high).** If a specific strategy type (e.g., trend following) is consistently losing across all coins, it should be disabled as a class. Currently each bot is evaluated independently.

**Signal quality threshold (medium).** The portfolio allocator accepts any bot that isn't PAUSED and has profit_factor > 0 and win_rate > 0. A bot with 1% win rate and 0.01 profit factor would still receive capital.

**Shadow mode / approval mode (medium).** The system should be able to generate proposed trades and wait for human approval before executing. Currently it is fully autonomous once enabled.

**Trade frequency limits (medium).** No maximum on how many orders can be placed per hour or per day. A bug or volatile market could trigger hundreds of orders.

**Volatility filter (medium).** The system does not check whether current market volatility is within normal ranges before trading. It will rebalance during a liquidation cascade or exchange outage with the same logic as during calm markets.

**Cooldown period after losses (medium).** After a losing day, the system should reduce position sizes or wait before re-entering. Currently it immediately rebalances to full target allocations.

**Live vs. paper performance comparison (low but important for trust).** Once live trading starts, the dashboard should show paper and live results side by side to detect execution quality differences.

**Correlation clustering (low but important for accuracy).** The diversification score is meaningless without measuring actual return correlations between positions.

---

## 5. Does the Architecture Make Sense?

The concept of "202 bots across 28 coins all competing at once" sounds like sophisticated diversification. In practice, it is chaos disguised as sophistication.

Here is why. You have 28 coins, each with 7 strategy types. But all 7 strategies for a given coin are making directional bets on the same underlying asset. A scalper, a momentum bot, and a trend follower on BTC are all exposed to BTC's price movement. When BTC drops 10%, the scalper might lose 2%, the momentum bot might lose 8%, and the trend follower might lose 12%. They do not hedge each other.

True diversification would require uncorrelated return streams. In crypto, most altcoins are highly correlated with BTC (typical correlation 0.6-0.9). So even across 28 "different" coins, a broad market selloff hits everything simultaneously.

The equal-weight allocation makes this worse. A properly risk-managed system would allocate based on inverse volatility, or risk parity, or at minimum weight by signal quality. This system gives the same capital to a grid bot with 80% win rate as to a swing bot with 40% win rate.

The number 202 also creates operational risk. Every 30 minutes, the system evaluates 202 bots, generates allocations, aggregates them into ~24 positions, and executes rebalancing trades. Any bug in any part of this pipeline — and there was one — affects every dollar in the account.

A simpler system with 5-10 carefully selected, thoroughly tested strategies on 5-8 liquid coins with proper risk controls would almost certainly outperform 202 bots with no risk controls. Complexity is not an edge; it is a liability when risk management is absent.

---

## 6. Page-by-Page Dashboard Critique

**Overview:** Should prove the system is making money and within risk tolerances. Currently shows KPIs (win rate, Sharpe, etc.) derived from seed data, not live performance. A user looking at this page has no way to distinguish simulated metrics from real results. Verdict: misleading.

**Portfolio:** Should prove capital is allocated intelligently with proper diversification. Currently shows equal-weight allocation with composite scores that are not used for weighting. Does not show correlation between positions, concentration risk, or sector exposure. Verdict: decorative.

**Alpaca Trading:** Should prove the broker connection is working and trades are executing correctly. This is the most useful page — it shows real Alpaca account data, positions, and order history. But it lacks P&L attribution (which strategies drove gains/losses) and does not show cumulative trading costs. Verdict: functional but incomplete.

**Strategy Scorecard:** Should prove each strategy has a genuine edge. Shows win rate, profit factor, Sharpe ratio, and adaptation scores. But all metrics come from seed data, not real trading. A user cannot tell which strategies are actually profitable versus which ones had favorable random seeds. Verdict: untrustworthy until backed by real data.

**Bot Signals:** Should prove the system is generating actionable, high-quality signals. Shows current verdicts (HOLD/PAUSE/REACTIVATE) for each bot. Does not show signal hit rate, false positive rate, or how often signals conflict with each other on the same coin. Verdict: surface-level.

**Performance Analytics:** Should prove the system generates positive risk-adjusted returns over time. Should show equity curves, drawdown charts, monthly returns, and benchmark comparisons. Currently shows per-bot metrics without portfolio-level performance attribution. Verdict: missing the most important information.

**Learning Engine:** Should prove the system improves over time. Shows adaptation scores that, until the recent fix, were all 60-65. Even with the fix, scores are computed from seed data metrics, not from observing how strategies respond to real regime changes over time. The engine needs months of real data to produce meaningful signals. Verdict: premature.

**Market Regime:** Should prove the system correctly identifies market conditions and adjusts accordingly. Uses a simple statistical classifier that has a dead code path (the "choppy" regime condition that can never trigger). Does not validate regime predictions against subsequent market behavior. Verdict: broken.

**Decision Log:** Should prove the system makes defensible decisions with clear reasoning. Shows verdict history with reasons. This is one of the more useful pages for audit purposes, but it does not show whether decisions were correct in hindsight. Verdict: adequate for logging, not for validation.

---

## 7. Ranked Root Causes of the 64% Loss

**Rank 1: The allocation overwrite bug.** Confirmed. `target_by_symbol` was being overwritten instead of accumulated, so each coin got 1/7th of its intended allocation. The rebalancer then sold existing positions down to these tiny targets, realizing losses on every cycle. This alone can explain the majority of the loss. Evidence: the bug was found and fixed in `alpaca_trader.py`.

**Rank 2: No drawdown circuit breaker.** With no max loss halt, the system kept trading through the entire drawdown. Each 30-minute cycle compounded the damage. Evidence: `alpaca_auto_trader.py` has no equity check before executing.

**Rank 3: Destructive rebalancing frequency.** Every 30 minutes, the system sold positions to match undersized targets (due to bug #1), buying them back larger in the next cycle when a new analysis ran, then selling again. This churn destroyed value through repeated round-trips at market prices. Evidence: 15% rebalance threshold in `alpaca_trader.py` would be triggered constantly by the 7x allocation mismatch.

**Rank 4: No position-level stop losses.** Individual positions had no downside protection. A 20% drop in any coin was fully absorbed. Evidence: no stop-loss logic anywhere in the codebase.

**Rank 5: Equal-weight allocation to low-quality strategies.** Strategies with 38-48% win rates received the same capital as those with 68-82% win rates. The losing strategies dragged down the portfolio while receiving equal funding. Evidence: `portfolio_allocator.py` line 87-90.

**Rank 6: Market correlation not accounted for.** During a broad crypto selloff, all 28 coins dropped together, and all 202 bots lost money simultaneously. The system assumed diversification across coins equals diversification across risk. Evidence: no correlation analysis in the codebase.

**Rank 7: Trading costs not modeled.** With 24+ positions rebalancing every 30 minutes, cumulative spread and slippage costs could be 1-3% per day. Over 3 days that is 3-9% of portfolio value just in friction. Evidence: `_estimate_monthly_return` has no cost deductions.

**Rank 8: Learning engine produced no protective signal.** Adaptation scores clustered at 50-65, providing no differentiation between strong and weak strategies. The enhanced verdict system defaulted to HOLD for almost everything. Evidence: `learning_engine.py` requires regime-specific trade history that did not exist.

**Rank 9: Regime detection failure.** The regime detector may have misclassified market conditions, causing the system to hold trend-following strategies during a mean-reverting market or vice versa. The broken "choppy" condition means an entire regime type is undetectable. Evidence: `_classify_regime` line 259.

**Rank 10: Simulated data masking real performance.** The dashboard showed favorable metrics from seed data while actual trading was losing money. This prevented the developer from noticing the problem earlier. Evidence: `seed_data.py` generates all performance history from random numbers.

---

## 8. Should This System Be Paused?

**Yes. Immediately. Live trading should be disabled until every item below is addressed.**

Specifically:

Do not run this with real money under any circumstances in its current state. The bug fix addressed one mechanical failure, but the absence of risk controls means the next bug, market event, or bad signal will produce a similar outcome.

Keep paper trading enabled only as a controlled test with frequent monitoring — check results daily, not monthly. Set the auto-trade interval to 24 hours minimum, not 30 minutes.

Reduce the bot count to 10-20 of the highest-quality strategies on the 5-8 most liquid coins. Run this reduced set for 90 days in paper mode.

Do not scale back up until the reduced set demonstrates consistent, auditable profitability.

---

## 9. Recovery Plan

**Immediate emergency fixes (do this week):**

Add a hard equity circuit breaker to `alpaca_auto_trader.py`: before any trade cycle, check if account equity is below 80% of peak equity. If so, close all positions and disable auto-trading. This is 10 lines of code and prevents catastrophic loss.

Add a daily loss limit: if today's losses exceed 5% of starting-day equity, halt trading for the day.

Fix the broken regime detection: the `1.5 <= cv <= 1.5` condition on line 259 of `learning_engine.py` should probably be `1.0 <= cv <= 2.0` or similar.

Reduce auto-trade frequency from 30 minutes to 4-24 hours. Crypto does not require 30-minute rebalancing for a portfolio-level system.

**Short-term structural fixes (next 2-4 weeks):**

Replace equal-weight allocation with risk-parity or inverse-volatility weighting. Strategies with higher drawdown or lower Sharpe should receive less capital.

Add per-position stop losses. If any single position drops 10% from entry, close it automatically.

Add portfolio concentration limits: no single coin > 15% of portfolio, no single strategy type > 30%.

Implement a signal quality threshold: require minimum win rate > 45%, profit factor > 1.1, and Sharpe > 0.3 for any strategy to receive capital.

Reduce to 30-50 bots maximum. Remove redundant strategy types on the same coin unless they have demonstrably uncorrelated returns.

Build a real performance tracking system that records every fill, every P&L, every fee, attributed to each strategy. Stop relying on seed data.

**Longer-term validation improvements (1-3 months):**

Run paper trading for 90+ days with the structural fixes in place. Measure actual risk-adjusted returns, maximum drawdown, and strategy-level attribution.

Add correlation analysis: compute rolling 30-day correlations between all positions and flag when portfolio correlation exceeds 0.7.

Build a proper backtest framework that includes slippage, fees, and spread modeling. Validate that the strategies have positive expected value after costs.

Implement walk-forward validation: train on months 1-3, test on month 4, retrain on months 2-4, test on month 5, etc. This prevents overfitting.

Add a shadow mode where the system generates proposed trades but requires human approval for the first 30 days of any new strategy.

---

## 10. Real Acceptance Criteria for Live Trading

Before this system should touch a single dollar of real money, it must meet all of the following:

**Track record:** Minimum 90 days of continuous paper trading with no manual intervention, showing all fills verified against Alpaca statements.

**Risk-adjusted returns:** Sharpe ratio above 0.5 over the 90-day paper period. A Sharpe below 0.5 means the returns do not compensate for the risk taken.

**Maximum drawdown:** Paper trading max drawdown must not exceed 20% at any point during the 90-day test. A system that draws down 20% in paper trading will draw down 30%+ in live trading due to worse execution.

**Automated risk controls:** All of the following must be implemented and tested: equity circuit breaker (halts at -15% from peak), daily loss limit (halts at -5% daily), position stop losses (-10% per position), portfolio concentration limits, and a kill switch that closes all positions within 60 seconds.

**Strategy retirement rules:** Any strategy that underperforms its benchmark (buy-and-hold of the same coin) for 30 consecutive days should be automatically disabled and not re-enabled without manual review.

**Cost analysis:** Demonstrated that cumulative trading costs (fees + slippage) are less than 20% of gross profits. If trading costs consume more than 20% of profits, the system is churning.

**Regime validation:** The regime detector must show at least 60% accuracy in classifying market conditions, validated against a held-out data set.

**Bug-free execution:** Zero critical bugs discovered during the final 30 days of paper testing. The allocation overwrite bug would reset this clock.

**Scaling plan:** Start live trading with a maximum of $1,000. Increase capital by no more than 2x per month, only if the prior month was profitable. Reaching $100,000 allocation should take a minimum of 7 months.

---

## Final Verdict

**Would I trust this system with my own money right now? No. Absolutely not.**

This system has a sophisticated-looking dashboard wrapped around a fragile execution engine with no risk controls. It lost 64% in 3 days due to a single code bug that went undetected because there are no automated checks, no alerts, and no circuit breakers. The learning engine produces no meaningful signal. The regime detector has dead code. The portfolio allocator computes quality scores and then ignores them. The performance data is simulated. And there is nothing — not a single line of code — that would prevent the system from trading the account to zero.

The dashboard looks professional. The architecture sounds impressive. But none of that matters when the system cannot answer the most basic question a risk manager would ask: "What happens when something goes wrong?" The answer right now is: nothing stops it. It keeps trading. And you lose your money.

Fix the risk controls first. Build a real track record second. Then — and only then — consider real capital, starting very small.
