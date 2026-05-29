# Strategy Factory — Real-Money Readiness Audit

**Date:** May 28, 2026
**Reviewer goal (from Ved):** Prove the system actually works before committing real money. Start small (< $5k). Currently only live paper, no backtest.
**Scope:** Whether this system is ready to trade real money — not whether it runs.

---

## Bottom line

The system is **well-engineered as software** and **safe by construction** (paper-only is hardcoded, risk controls are layered and actually wired into the live path). But it is **not ready for real money**, and the reason is singular and decisive:

> **Nobody has proven this system makes money.** There is no backtest of the current strategies, and the live paper track record has never been audited for net-of-cost expectancy.

Everything else in this report is secondary to that. If we run a proper validation and the edge isn't there, none of the other fixes matter. If the edge *is* there, the rest of this list is what stands between "looks profitable on paper" and "survives real money."

I have **not changed any code** this session, per your instruction.

---

## BLOCKERS — do not risk a dollar until these are resolved

### B1. No proof of edge (the core issue)
- `archive/real_backtest_results.json` is **empty (`[]`)** — the backtest never produced results.
- The archived backtest (`archive/real_backtest.py`) tested **crypto coins with simple EMA crossovers** — a different universe and different strategies than the current 10-ETF / 10-strategy system. It tells us nothing about what's running now.
- The local trade ledger (`data/alpaca_trade_ledger.csv`) has **a header and zero rows**. `trade_journal.json` and `trading_desk_decisions.json` are empty. The real paper history exists only on the Railway persistent volume and has never been pulled down and analyzed.
- The dashboard's strategy-performance numbers are **seed-data placeholders** (confirmed in the prior audit — they still show pre-ETF symbols like AAPL, SOL/USD), i.e. not real results.

**What "proven" must mean before real money:**
1. Pull the **actual Railway paper trade ledger** (every closed trade since the ETF switch) and compute, net of fees: total trades, win rate, average win/loss, **profit factor**, **expectancy per trade**, max drawdown, and per-strategy / per-ETF breakdown.
2. Independently **backtest all 10 strategies on all 10 ETFs on 1H bars** over a multi-regime window (include a real drawdown like a correction), out-of-sample, with realistic costs (see B2). The current code has no working backtest for this.
3. Decision rule: only proceed if **both** the audited paper record **and** the backtest show a positive, fee-adjusted edge with a drawdown you can stomach on < $5k.

### B2. Cost model is optimistic and unvalidated
- Fees are modeled as **1 basis point per side as a slippage proxy** (`EST_FEE_PCT_STOCK = 0.01`). Alpaca paper fills are effectively frictionless; **real fills are not.**
- Real costs hit hardest exactly where this system trades: leveraged ETFs (SOXL/TQQQ/SOXS) and small-cap IWM have wider spreads and more slippage; 1bp will understate real cost, possibly by a lot.
- With a sub-$5k account and a **$5 minimum order**, per-trade friction and rounding are a large fraction of each position — small edges get eaten alive.

**Before real money:** measure realized slippage from paper fills vs. expected price, and re-run the expectancy in B1 with **conservative** real-world costs. If the edge only exists at 1bp, it isn't an edge.

---

## HIGH — correctness and behavior that could mislead the go/no-go decision or hurt real capital

### H1. The daily-loss limit is documented three different ways
The code actually implements a **ladder**, but it's never stated coherently:
- Conservative mode **tightens** to 90+ quality trades at **−0.5%** daily (`DAILY_LOSS_THRESHOLD_PCT`).
- `DailyLossGuard` **hard-blocks** new trades at **−2%** (the real default in `RiskManager.__init__`).
- The prior audit text says **5%** (a stale docstring).

This isn't necessarily a bug, but for real money you must know **exactly** what stops you out. Pick the intended ladder, document it as the single source of truth, and make the code and docs agree.

### H2. EOD logic is behaviorally backwards for a trend system
`eod_manager.py` **sells every green position at 3:45 PM ET daily and keeps red ones overnight.**
- This **cuts winners short** (capped at < 1 day) and **lets losers run** with overnight gap risk — the textbook disposition effect, encoded in the system.
- Your strategies (trend pullback, breakout, supertrend continuation, Donchian) are designed to ride multi-day moves on 1H bars. Forcing a same-day exit on winners likely **removes the very edge they're built to capture.**
- It also interacts badly with B1/B2: paper doesn't model overnight gaps well, so live behavior on the kept-overnight losers could diverge sharply from paper.

**Action:** Treat "sell green / keep red EOD" as a deliberate hypothesis and A/B test it in the backtest (with vs. without). Don't assume it helps.

### H3. Timezone mismatch on risk resets (still unfixed from prior audit)
`DailyLossGuard` and `TradeFrequencyLimiter` reset at **midnight UTC**; conservative mode and EOD use **midnight/markets ET**. Between ~7 PM and midnight ET the daily counters disagree about what "today" is, which can let extra trades through or reset the loss guard early. Fix to a single ET reset before relying on daily limits with real money.

### H4. The learning engine has been reset due to corrupted P&L, and still drives strategy selection
`trading_desk.py` auto-resets the learning state on deploy via a flag file, with a comment noting a **prior "partial profit accounting bug caused massively overstated losses" that poisoned strategy scores.** Strategy ranking partly depends on these scores. Confirm the current learning state is clean and rebuilt from **correct** P&L before trusting any auto-weighting — otherwise the system may be favoring/avoiding strategies for the wrong reasons.

---

## MEDIUM — understand and plan for, not necessarily pre-launch blockers

- **M1. Long-only / bull-biased.** Every strategy is long. The only "short" is buying SOXS (−3x semis), which suffers volatility decay and is a poor hedge. In a sustained downturn the system's real risk-off is going to cash. Fine for a small test, but know it's structurally bullish.
- **M2. Broker must be the source of truth.** Reconciliation only *warns* at a >$5 internal-vs-Alpaca discrepancy. With real money, internal accounting must match the broker exactly; treat Alpaca's numbers as authoritative and alert (not just log) on any mismatch.
- **M3. Single daemon, no supervision.** The trading loop is one background thread on Railway. Today's incident (QQQ/IWM/VOO not sold because the loop slept through 3:45 PM) shows the failure mode is real even after the fix. Add: a heartbeat/alert if the trading thread dies or hasn't run a cycle in N minutes, and verify the kill switch end-to-end.
- **M4. Strategies were ported from a crypto system.** Comments still reference "Alpaca crypto" and 24h volume assumptions. ETF regular-trading-hours sessions with overnight gaps behave differently from 24/7 crypto; volume/VWAP/ATR assumptions should be re-validated for 1H ETF bars.

---

## LOW — cleanup (cosmetic, mostly carried over from the prior audit)

- Dead routes `/api/alpaca/auto/preview` and `/api/alpaca/auto/execute` still import the **old `AlpacaTrader`** — a foot-gun if ever hit; delete them.
- Stale config (`BINANCE_BASE_URL`, `INTRADAY_TIMEFRAMES`) and dead Binance paths (`api_client.py`, parts of `intraday_engine.py`).
- Seed-data pollution showing pre-ETF symbols on the dashboard.
- Out-of-date comments ("crypto trading system", "100 stocks + 3 crypto").

---

## What's genuinely solid (so we don't break it)

- **Paper-only is hardcoded** (`paper=True`, paper base URL) — no accidental live trading.
- Risk **position sizing is actually wired** into the live path: per-trade risk %, ATR-stop geometry, regime/confidence/cooldown multipliers, 8% per-position and exposure caps, leveraged-ETF halving, $5 min order, min 1.5 risk:reward.
- Layered guardrails exist and are invoked: circuit breaker (15% drawdown), daily loss guard, exposure limits, frequency limiter, duplicate guard, cooldown, strategy disabler.
- Closed-candle rule and duplicate-signal prevention are in place.
- EOD now polls every 10s (today's timing bug fix) and there's a working kill switch.

---

## Recommended sequence to "prove it works"

1. **Pull the real Railway paper ledger** → compute net-of-fee expectancy, profit factor, max drawdown, per-strategy/per-ETF. (Answers B1 for live paper.)
2. **Build a real backtest** of the 10 strategies × 10 ETFs on 1H bars, out-of-sample, with conservative costs, including a drawdown regime. A/B the EOD rule (H2). (Answers B1/B2 properly.)
3. **Reconcile the two.** If live paper and backtest agree and show a real fee-adjusted edge → proceed to a tiny real pilot. If they disagree → trust neither until you understand why.
4. **Only then** fix H1/H3/H4, add M3 supervision, and start with the smallest real size that's psychologically real.

If you want, my next step is **#1 — pull and audit the actual Railway paper trade history** so we replace placeholders with real numbers. That's the fastest way to learn whether there's anything here worth backtesting.
