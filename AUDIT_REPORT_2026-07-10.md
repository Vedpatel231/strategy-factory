# Strategy Factory — One-Month Deep Audit Report
**Date:** 2026-07-10 · **Mode:** Alpaca paper (verified) · **Audit type:** Read-only. No code was changed.
**Evidence base:** live dashboard APIs, real Alpaca fills (FIFO, net of fees), trade journal, and source code.

---

## 0. Read this first — a universe mismatch that changes everything

Your instructions say the system "should be focused on" **QQQ, SPY, SOXL, IWM, TQQQ, SOXX, SMH, RSP, SOXS, VOO** (includes leveraged/inverse: SOXL, TQQQ, SOXS, plus SOXX/RSP/VOO).

The **deployed** system trades a completely different, **12-ETF long-only, NO-leverage** universe:

> QQQ, SPY, IWM, SMH, XLF, XLE, XLV, XLI, GLD, GDX, TLT, XBI

This is not a bug — it is the universe **you approved in a prior session** when we deliberately removed all leveraged/inverse ETFs (SOXL/TQQQ/SOXS) and near-duplicates (RSP/VOO/SOXX) for cleaner risk sizing. `config.py` and the live dashboard both confirm "12 ETFs Active", and `LEVERAGED_ETFS = set()` (empty).

**You have to decide which is correct before any tuning happens**, because Parts 4–9 depend on it. I did the audit against the **actually deployed** universe. My recommendation is to **keep the long-only universe** (leveraged ETFs would make the payoff-asymmetry problem below much worse), but this is your call. I have **not** changed it.

---

## 1. Executive Summary

- **Overall health:** Alive and healthy. App runs, dashboard loads, APIs respond, the 15-minute desk cycle ran today (15:22 UTC), Alpaca paper is connected, reconciliation is clean. No crashes, no broken imports, no duplicate workers.
- **Is the system safe in paper mode:** **Yes.** `paper=true` confirmed on the account, `dry_run=false` (orders are real paper orders), no live-trading path is active.
- **Is P&L trustworthy:** **Yes for the ETF era.** The calendar/realized numbers reconcile to real fills. One caveat: lifetime account equity ($32.7k vs $100k start) is dominated by **legacy April crypto losses** that the recent FIFO window doesn't fully reach — that ~$67k hole is not from the current ETF system.
- **Is the bot too strict, too loose, or balanced:** **Balanced on entries, badly unbalanced on exits.** It is not over-trading and not obviously missing big moves. The problem is **payoff asymmetry**: it cuts winners tiny and lets losers run to the stop.
- **Biggest problem:** **Inverted win/loss ratio.** Last month: profit factor **0.25**, win rate **38%**, average win **+$10.45** vs average loss **−$25.78** (worst −$68). Take-profit fired **once in 50 trades**; winners are exited early by regime-flip/signal-invalidation while losers ride to the −2% stop.
- **Biggest opportunity:** **Fix the exit logic so winners can run** (let a portion ride to the 3.5% target / trailing stop instead of closing green early), and **stop firing breakout/momentum bots in low-ADX chop.** These two changes target the exact leak without touching entries broadly.

**One-line verdict:** The plumbing is healthy and honest; the trading edge is currently negative because of exit asymmetry and regime-inappropriate entries — both fixable with targeted changes, not a rebuild.

---

## 2. System Health

**Working:**
- Container boots cleanly (`entrypoint.py`): seeds DB, prunes off-universe bots, generates dashboard, then `exec gunicorn` with **1 worker / 4 threads** — so exactly **one** auto-trader thread (no racing duplicate workers).
- Desk cycle (scheduler) is live: `updated_at = 2026-07-10T15:22 UTC`, interval 15 min (`DESK_CYCLE_INTERVAL_MIN=15`).
- All APIs respond 200 (`/api/alpaca/account`, `/positions`, `/conservative-status`, `/insight-data`, `/realized-by-day`).
- 120 bots / 12 managers instantiated this cycle. No frontend console errors observed.
- `py_compile` clean across the codebase (from prior suite run).

**Problems:** None critical. Minor: the conservative-status field `est_fee_pct_per_trade` displays the generic **0.10%** default, not the **0.01%** actually applied to ETFs — cosmetic but misleading.

**Fixes needed:** None urgent. Correct the displayed fee % label (P3).

---

## 3. Alpaca + Paper Trading

**Working:**
- **Paper mode active, live NOT active** — `paper:true`, `account_number PA3S9TTHFNHQ`, `trading_blocked:false`, `dry_run:false`.
- Account equity **$32,671.11**, cash **$32,671.11**, buying power **$32,671.11** — currently **100% cash, 0 open positions** (bot is flat).
- Orders/fills load correctly and are recorded; internal↔Alpaca **reconciliation status "OK"** (positions_internal 0 = positions_alpaca 0, discrepancy 0).

**Problems:**
- Lifetime equity is **−$67k from the $100k paper start**, but the ETF-era realized total is only **−$1,785**. The gap is **legacy April crypto** liquidated at a loss and not captured by the 500-order FIFO window. It's not a live bug, but it makes the account-level number look far worse than the ETF strategy actually is.

**Fixes needed:**
- (P1, needs approval) Optionally reset/segregate the paper account or clearly label "ETF-era P&L" vs "lifetime (incl. legacy crypto)" so the headline number reflects the system you're actually running.

**Current snapshot:** Equity $32,671.11 · Buying power $32,671.11 · Cash $32,671.11 · Open positions: none · Today (Jul 10) realized: −$66.15 · No order failures, no sync issues.

---

## 4. One-Month Performance (2026-06-10 → 2026-07-10)

All figures net of estimated fees (1 bp/side), FIFO-matched from real Alpaca fills.

| Metric | Value |
|---|---|
| **Net P&L** | **−$405.55** |
| Gross P&L | −$387.83 |
| Estimated fees | −$17.70 (negligible) |
| Trades (closed round-trips) | 34 |
| Wins / Losses | 13 / 21 |
| **Win rate** | **38.2%** |
| **Profit factor** | **0.25** |
| Average win | +$10.45 |
| Average loss | −$25.78 |
| **Win/loss ratio** | **0.41** |
| Largest win / loss | +$21.55 / −$68.49 |
| **Max losing streak** | **6** |
| Avg hold time | ~24.9 h (≈1 day) |
| Green days / Red days | 6 / 11 |
| Best day / Worst day | +$24.84 (Jul 1) / −$127.72 (Jul 8) |
| Best ETF (least bad) | GLD −$0.63, SPY −$0.77, QQQ −$6.95 |
| Worst ETF | XLV −$129.75, XLI −$58.83, XLF −$54.76 |
| Best strategy (lifetime ETF era) | donchian_breakout **+$305** (11% WR, one big win) |
| Worst strategy | breakout_retest −$220, atr_momentum_expansion −$210 |
| Best/Worst timeframe | Only **1H** runs — see Part 7 |
| Best/Worst regime | **100% of trades tagged "trending"** — see Part 8 |

**Did the bot improve?** No — the last month is net negative and the **most recent 4 days are all red** (Jul 7–10: −$68, −$128, −$68, −$66 = −$330).
**Profitable after fees?** No. Fees are trivial ($17.70); the loss is real strategy P&L.
**Why is it losing?** Not fees, not sizing-blowups, not over-trading. It's **(a) exits** (winners cut to +$10 avg, one TP hit in 50 trades) and **(b) entry selection** (breakout/momentum entries in a low-ADX, bearish-drift tape). Payoff asymmetry (0.41 win/loss ratio) at a 38% hit rate is mathematically a losing machine.
**Trading too much/little?** About right in frequency (34 trades/month, ~2/day max). It correctly sat flat today.
**Missing big opportunities?** No evidence of it — today's "missed" list has **$0 hypothetical P&L**; closest setups scored 52 vs the 75 gate. The bot is disciplined about *not* entering; the leak is on the trades it *does* take.

---

## 5. Strategy Review (ETF-era journal, net of fees)

| Strategy | Trades | Net P&L | Win rate | Verdict |
|---|---|---|---|---|
| donchian_breakout | 9 | **+$305.37** | 11% | **A. Keep** — low WR but one large trend win pays for many small losses. This is a working asymmetric edge. |
| trend_pullback | 2 | −$55.89 | 50% | **E. Need more data** — sample too small. |
| supertrend_continuation | 8 | −$43.00 | 25% | **B. Tune** — mild loser; tighten to strong-ADX only. |
| ema_crossover | 2 | −$105.43 | 0% | **E/D. Need data or limit** — 0/2, both full losers. |
| atr_momentum_expansion | 18 | **−$209.84** | 33% | **C. Limit** — highest trade count, second-worst P&L. Restrict to genuine high-ADX/high-ATR regimes only. |
| breakout_retest | 11 | **−$220.44** | 27% | **C. Limit / B. Tune** — worst net. Breakout retests keep failing in chop; gate behind real volume + trend confirmation. |

**Interpretation (important):** The two breakout-family momentum strategies (breakout_retest, atr_momentum_expansion) account for **−$430 of the losses across 29 trades**, while donchian (also breakout-family but with a wider target) is the only net-positive strategy because it let a winner run. The difference is **exit management**, not entry type: donchian's one win was allowed to grow; the others' winners were clipped early.

**Do not disable everything.** Targeted call: **limit** breakout_retest and atr_momentum_expansion to strong-trend conditions, **keep** donchian, and **gather data** on the thin-sample strategies before judging.

---

## 6. ETF Review (Jun 10 – Jul 10, net)

| ETF | Net P&L | Status recommendation | Reason |
|---|---|---|---|
| GLD | −$0.63 | **Keep** | Near breakeven; low correlation diversifier. |
| SPY | −$0.77 | **Keep** | Broad, liquid, near breakeven. |
| QQQ | −$6.95 | **Keep** | Broad, best signal quality. |
| TLT | −$24.79 | **Keep / watch** | Rates diversifier; small loss. |
| IWM | −$33.65 | **Watch** | Small-cap noise; single −$58 loser. |
| XBI | −$47.25 | **Limit** | High-vol biotech; repeated ~2% stop-outs. |
| GDX | −$48.18 | **Limit** | High-beta miners; choppy. |
| XLF | −$54.76 | **Limit/Watch** | Sector chop. |
| XLI | −$58.83 | **Limit** | Sector chop; −$68 single loser. |
| XLV | −$129.75 | **Limit** | **Worst ETF** — two −$67 stop-outs; defensive sector but traded as breakout. |

**Pattern:** Broad indices (SPY/QQQ) and GLD are ~flat; the damage is concentrated in **single-sector ETFs (XLV, XLI, XLF, GDX, XBI)** where breakout signals whipsaw. **SMH and XLE did not trade** in the window.

**Leveraged ETF handling:** Not applicable — SOXL/TQQQ/SOXS are **not in the deployed universe**. The 50%-size guard code exists but is dormant (`LEVERAGED_ETFS` empty). If you re-add leveraged ETFs (Part 0 decision), that guard would re-activate, but I'd advise against re-adding them given the current exit asymmetry.

---

## 7. Timeframe Review

**Reality check:** The system runs a **single entry timeframe — 1H** (`DESK_ENTRY_TIMEFRAMES=["1h"]`, `DESK_ENTRY_TIMEFRAME="1h"`). **15m and 30m are not enabled.** Confirmation TFs 4h/1D are configured but the intraday-confirmation list is empty. So there is **no 15m/30m data to compare** — the premise of a 15m/30m/1H comparison is outdated.

- **15m:** disabled. (Correctly so for a real-money-safety posture — 15m adds noise.)
- **30m:** disabled.
- **1H:** the only timeframe; candles are treated as closed before signals (desk cycle every 15 min evaluates the latest 1H bar).
- **Recommended default:** **Keep 1H as the sole entry timeframe.** It is the right choice for swing trades. Do **not** add 15m/30m until the exit/entry-quality problems below are fixed — faster timeframes would multiply the current whipsaw losses.

---

## 8. CEO / Manager / Bot Review

**Working:**
- Hierarchy is real and running: **CEO → 12 managers → 120 bots**. This cycle all 12 managers returned **WAIT** (0 entries) — appropriate for today's read.
- CEO regime logic is **not stuck**: current live read is **regime "sideways", direction "bearish", ADX 19.4, ATR 0.59%, confidence 0.66** ("4/12 bullish, 8/12 bearish"). It varies correctly with the tape.
- Managers correctly reject weak setups (today's closest bots scored 52 vs 75 gate).

**Problems (this is the core diagnosis):**
- **Every one of the last 50 entries was tagged regime "trending"** — yet those trend/breakout entries systematically failed. The "trending" label requires only **ADX ≥ 22** with a bullish tilt. In a market that spends most of its time at ADX ~19 with brief pops above 22, this threshold **over-labels "trending"** and fires momentum bots on false continuations that immediately revert.
- **Mean-reversion strategies never fire.** The CEO's own instruction set prefers `rsi_mean_reversion / bollinger_reversion / vwap_bounce` in ranging/sideways regimes — but **zero** mean-reversion trades occurred in 50 closes. So in the choppy regime that actually dominates, the system either takes the *wrong* (breakout) trade or nothing. The mean-reversion path is effectively dead weight.
- **Regime-flip exits clip winners.** 9 of 13 winners exited on "regime-flip: CEO regime changed" at ~+$10. The CEO flipping trending↔sideways repeatedly means positions are opened on a pop and closed on the revert — buying the top of micro-moves.

**Fixes needed:**
- Raise/condition the "trending" bar (e.g., ADX ≥ 25 **and** rising, plus ATR floor) so momentum bots only fire in real trends. (P1)
- Make the mean-reversion bots actually produce entries in ranging regimes, or accept that in chop the correct action is **no trade**. (P2)
- Stop letting a single regime-flip force-close a green position that hasn't hit its stop or target. (P1)

---

## 9. Missed Opportunities

**Examples (today, 2026-07-10):** 24 "missed" symbols but **hypothetical P&L $0** — closest setups were VWAP Bounce (mean-reversion) bots on QQQ/SPY/IWM/SMH scoring **52.4 (conf 0.30)**, well below the 75 gate. Top "missed": QQQ (7), XLF (6), SMH (4), GLD (4), XLI (3).

**Likely cause:** These are **not real missed profits** — they are correct "no clean setup" waits in a low-ADX tape. The missed-opportunity tracker is counting *scans that didn't trigger*, not *profitable moves the bot failed to catch*.

**Fixes:** None needed on entries — the bot is **not** the "sat flat during an obvious rip" case. The honest finding is the opposite of a missed-opportunity problem: it should have *waited more* on the breakout trades it took. (Minor P3: relabel the tracker so "missed" reflects moves with real hypothetical P&L, not every non-trigger.)

---

## 10. Losing-Trade Patterns

**Patterns (last month's worst 5 are nearly identical):**
- XLV −$68 (2% stop, 50h hold), XLF −$68 (2%, 24h), XLI −$68 (2.4%, 26h), XLV −$67 (2.4%, 46h), XBI −$66 (2.2%, 23h).
- Structure: **buy a sector ETF on a breakout/momentum signal → hold ~1 day → get stopped at ~−2%.** 15 of the losers exited at stop-loss; 7 timed out at the 96h max-hold; 11 on signal-invalidation.

**Causes:**
1. **Exit asymmetry (primary):** winners exit early (regime-flip/signal-invalidation at +$10), losers run to the −2% stop. Take-profit reached once in 50 trades.
2. **Regime-inappropriate entries (secondary):** breakout/momentum bots firing at ADX ~22 in a bearish-drift, low-vol tape.
3. **Sector concentration:** losses cluster in single-sector ETFs (XLV/XLI/XLF/GDX/XBI), which whipsaw more than broad indices.
4. **Not fees, not sizing blow-ups, not CEO letting through crazy setups** — the setups scored ≥75; they're just low-quality *in this regime*.

**Fixes:** Parts 8 (entry regime gate) + 12 (let winners run) address all four. No evidence of a bad-stop-too-tight problem — a 2% stop on a 1H swing is reasonable; the issue is that winners aren't allowed to reach a matching reward.

---

## 11. Fee / Net P&L Review

**Working:**
- Fee model is correct and ETF-appropriate: `EST_FEE_PCT_STOCK = 0.01%` (**1 bp per side**) for equities; crypto fee (0.20%) is separate and unused. Realized-by-day and the calendar use 1 bp/side consistently.
- Net = gross − fees everywhere checked (daily protection, calendar, realized-by-day). Measured: **$17.70 fees on 34 trades ≈ $0.52/trade** — matches ~1 bp on ~$3k×2 sides.
- Alpaca paper charges $0 commission; the 1 bp is a **slippage proxy** — realistic, arguably slightly conservative for liquid ETFs.

**Problems:**
- `conservative-status` reports `est_fee_pct_per_trade: 0.10` (the generic default), **not** the 0.01% actually applied to ETFs — makes fees look 10× higher than reality on that one field. Cosmetic.
- No strategy is "profitable gross but unprofitable net" — gross (−$387.83) and net (−$405.55) are close; **fees are not the culprit.**

**Fixes:** Correct the displayed fee-% field (P3). Otherwise fee logic is sound — **do not change it.**

---

## 12. Daily P&L Protection Review

**Working (as designed):**
- Thresholds live and equity-based: **+1% = +$326.71**, **−0.5% = −$163.36**; after either, required score jumps **75 → 90**, with explicit "no revenge trading" copy. Matches your spec exactly.
- Uses **net** P&L (gross − fees).

**Problems:**
- **It never triggered in the last month** because no single day breached −$163 — yet the account still bled **−$405** through a string of −$50 to −$128 days and a **6-trade losing streak**. The daily breaker only catches one catastrophic day; it does nothing about **slow bleed / multi-day streaks**.

**Fixes (P1, targeted — do not tighten the daily numbers):**
- Add a **rolling/streak guard**: e.g., after **N consecutive losing trades** or a **rolling 3-day net < −1%**, step required score to 90 (or pause new entries for a cooldown) until a green close. This plugs the exact hole the month exposed without making the single-day rule stricter.
- Keep +1%/−0.5% single-day thresholds **as-is** — they're reasonable.

---

## 13. Risk Management Review

**Working well:**
- Risk-per-trade base **0.15%** of equity, max **5** open positions, max open risk **0.5%** ($163), min R:R **1.5**, post-loss cooldown 2 bars, min order notional guard, duplicate/position guards, reconciliation, manual close controls, emergency close-all. This is a genuinely **conservative** framework.

**Too loose / a real bug:**
- **Position sizing silently exceeds the risk budget.** In `risk_manager.py` the risk-based notional (targeting 0.15% = ~$49) is then **multiplied** by `regime_mult × confidence_mult × cooldown_mult`. With an aggressive CEO (1.15) and high confidence (up to 1.20), that's up to **~1.38×**, pushing actual per-trade risk to **~0.20–0.21%** (~$66) — which is exactly the observed loss size. The multipliers scale *up from* the risk floor instead of *down from* a cap, so the "0.15% per trade" guarantee is not actually enforced. (P1)

**Too strict:** Nothing material — the framework is appropriately conservative for paper→real transition.

**Missing:**
- Streak/rolling drawdown guard (see Part 12).
- A cap that re-derives risk **after** the multipliers so per-trade risk can't exceed, say, 0.15–0.18%.

---

## 14. Dashboard Review

**Working:** Overview, Alpaca, and Claude Analysis pages load. Net P&L is clearly shown; the P&L calendar now shows one reconciled net-realized number per weekday (fixed last session). Reconciliation status and daily mode are surfaced. Trade reasons and rejection reasons are present in the insight data.

**Problems / confusing:**
- The **fee %** field shows 0.10% vs the real 0.01% (Part 11).
- **Lifetime vs ETF-era P&L** is not distinguished, so the legacy-crypto hole makes the headline account number look like the ETF system's fault.
- "Missed opportunities" counts non-triggers with $0 hypothetical P&L (Part 9) — reads scarier than it is.

**Add / improve (P3):**
- A small "ETF-era performance" panel (net P&L, win rate, profit factor, avg win/loss, current streak) so the numbers in this report are visible at a glance day-to-day.
- Per-strategy and per-ETF net P&L tables (the data exists; surface it).

**Remove/simplify:** Nothing urgent. Don't remove any page.

---

## 15. Telegram Review

**Working:** `telegram_notifier.py` is well-built (auto-splits long messages, HTML/plain support). Wired to: **risk alerts** (risk_manager), **daily report + failure alert** (dashboard_server), **conservative-mode threshold alerts** (conservative_mode), and **EOD summary** (eod_manager). P&L in these is net-of-fees.

**Problems:**
- **Configuration can't be verified from here** — it depends on `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` env vars on Railway (not in the repo, correctly). If you're not receiving messages, those env vars are the first thing to check.
- **No per-trade entry/exit alert** — you get daily/EOD/threshold/risk messages, but **not** a ping when a position opens or closes. For a bot bleeding on individual trades, a concise per-close alert (symbol, net $, reason) would give you real-time visibility.

**Fixes (P3):** Confirm the env vars are set; add an optional concise per-trade-close alert (net $ + exit reason). Keep volume low — one line per close.

---

## 16. Code Quality Review

**Critical:** None. No broken imports, no crypto trading path active, single worker (no duplicate auto-traders), tests pass.

**Important:**
- **Position-sizing multiplier bug** (Part 13) — behavioral, should be fixed.
- **Universe mismatch** vs your stated intent (Part 0) — a decision, not a code defect.
- **Dead mean-reversion path** — the strategies exist and are "preferred" in ranging regimes but never fire; either wire them up or stop advertising them.

**Optional:**
- Legacy files present but unused (`api_client.py`, `analytics.py`, `decision_engine.py`, `portfolio_allocator.py`, `daily_trade_analysis.py`, crypto-era comments). Prior work already archived the big ones; these are low-priority cleanup.
- Fee-% display field (cosmetic).
- `EST_FEE_PCT_PER_TRADE` default (0.10%) vs stock (0.01%) — harmless but confusing; document it.

**Do not delete anything yet** — this is a report only.

---

## 17. Recommendations

Each item: what · why · expected benefit · risk · effect on trade frequency · how to test · approval needed.

### P0 — Must fix immediately (safety/correctness)
**None.** The system is safe in paper mode, P&L is honest, reconciliation is clean. Nothing requires an emergency fix. (This is the honest finding — I'm not inventing a P0 to look busy.)

### P1 — High-impact performance
1. **Enforce the per-trade risk cap after multipliers** (risk_manager.py).
   *Why:* multipliers push risk from 0.15% to ~0.21%; that's the difference between −$49 and −$66 losers. *Benefit:* ~25–30% smaller losers, tighter risk. *Risk:* slightly smaller positions on high-confidence trades. *Frequency:* unchanged. *Test:* recompute sizing on the last 34 trades; confirm max risk ≤ target. *Approval:* **yes.**
2. **Let winners run** — stop force-closing green positions on regime-flip/EOD before they reach target or trailing stop; keep the partial-profit + ATR trailing that already exists.
   *Why:* take-profit hit once in 50 trades; avg win is +$10 vs the 3.5% target. *Benefit:* raises win/loss ratio from 0.41 toward 1.0+, the single biggest lever. *Risk:* a few green trades could round-trip to breakeven; mitigated by trailing stop + break-even-after-partial. *Frequency:* unchanged (slightly longer holds). *Test:* replay last month with "hold green to trailing stop" vs "close green early" and compare expectancy. *Approval:* **yes — this touches the EOD-green rule you designed, so I will not change it without your sign-off.**
3. **Raise the "trending" bar for momentum entries** (market_ceo.py): require ADX ≥ 25 **and rising**, plus an ATR floor, before trend/breakout bots may fire.
   *Why:* ADX≥22 over-labels chop as trending. *Benefit:* fewer false-continuation entries in low-ADX tape (the recent 4-day bleed). *Risk:* fewer trades; some real early trends missed. *Frequency:* **lower.** *Test:* count how many of the 21 losers would have been blocked at ADX≥25. *Approval:* **yes.**
4. **Add a streak / rolling-drawdown guard** to daily protection (conservative_mode.py): after ~4 consecutive losing trades or rolling-3-day net < −1%, require 90-score until a green close.
   *Why:* the daily −0.5% breaker never fired while the account slow-bled −$405 over a 6-loss streak. *Benefit:* caps multi-day bleeds. *Risk:* pauses trading during rough patches (intended). *Frequency:* **lower during losing streaks only.** *Test:* simulate over June–July; confirm it would have paused after Jul 7–8. *Approval:* **yes.**

### P2 — Strategy tuning (targeted, evidence-based)
5. **Limit breakout_retest and atr_momentum_expansion** to strong-trend regimes (ADX≥25, volume≥1.25×) and to **broad ETFs (SPY/QQQ/IWM)**; restrict them on single-sector ETFs (XLV/XLI/XLF/GDX/XBI) where they lost −$430. *Frequency:* lower. *Approval:* **yes.**
6. **Keep donchian_breakout as-is** (+$305, the one asymmetric winner) — do not "fix" its 11% win rate. *Approval:* no (no change).
7. **Either wire up the mean-reversion bots** (rsi/bollinger/vwap) so they actually fire in ranging regimes, **or** accept "no trade" in chop and stop preferring them in instructions. *Approval:* **yes**, decide direction.
8. **Gather more data on trend_pullback / ema_crossover** (2 trades each) before any verdict. *Approval:* no.

### P3 — Dashboard / reporting
9. Add an **"ETF-era performance" panel** (net P&L, win rate, profit factor, avg win/loss, current streak) + per-strategy / per-ETF net tables. *Approval:* optional.
10. Fix the **fee-% display** (show 0.01% for ETFs) and **separate lifetime vs ETF-era P&L**. *Approval:* optional.
11. Optional **per-trade-close Telegram alert** (symbol, net $, reason). *Approval:* optional.

### P4 — Cleanup / refactor
12. Archive unused legacy modules (`api_client`, `analytics`, `decision_engine`, `portfolio_allocator`, `daily_trade_analysis`) after confirming no imports. *Approval:* yes before deleting.
13. Document the fee constants and remove stale crypto-era comments. *Approval:* no.

**Not recommended:** tightening every threshold, loosening every gate, adding 15m/30m now, re-adding leveraged ETFs, or touching the fee/net-P&L or daily-protection *core* logic. None of those target the actual leak.

---

## 18. Best Next Step

- **Fix first:** the **exit asymmetry (P1 #2)** — it's the biggest single lever (moves win/loss ratio from 0.41 toward 1.0). Pair it with **P1 #1 (risk cap)** and **P1 #3 (trending bar)**. These three together attack the exact math that's losing money.
- **Do NOT touch:** fee/net-P&L logic, the +1%/−0.5% daily thresholds, Telegram, conservative mode core, the dashboard pages, and — unless you decide otherwise in Part 0 — the long-only universe. Don't add leveraged ETFs or faster timeframes yet.
- **Monitor next trading day:** whether momentum bots fire in the current **sideways/bearish, ADX-19** tape (they shouldn't much), the size of any losers vs the 0.15% target, and whether any winner is allowed to reach its trailing stop instead of being clipped at +$10.
- **Needs your approval before I change anything:** the Part 0 universe decision, and P1 #1–4 + P2 #5,#7 (all flagged "yes" above). I will not modify code until you pick which of these to proceed with.

---

*Prepared read-only from live Alpaca fills, dashboard APIs, the trade journal, and source code on 2026-07-10. No trading, no code changes, no data deletion were performed.*
