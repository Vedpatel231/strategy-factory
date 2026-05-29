# Strategy Factory — Deep System Audit Report

> **⚠️ SUPERSEDED IN PLACES (corrections as of May 28, 2026).** Two items
> flagged below have since been verified against the live code and are
> **no longer accurate**:
> 1. **Timezone mismatch — FIXED.** `DailyLossGuard` and
>    `TradeFrequencyLimiter` in `risk_manager.py` now use ET
>    (`_et_now()` / `_today_str()`), aligned with conservative mode and the
>    EOD manager. The "resets at midnight UTC / RISKY" notes are stale.
> 2. **Daily-loss limit is 2%, not 5%.** The hard `DailyLossGuard` block is
>    **−2%** (RiskManager constructs it with `max_daily_loss_pct=2.0`). The
>    "5% max daily loss" lines below are stale.
>
> See `REAL_MONEY_READINESS_AUDIT.md` for the current, authoritative review.

**Date:** May 27, 2026  
**System:** Strategy Factory ETF-Only Trading System  
**Deployment:** Railway (strategy-factory-production-9843.up.railway.app)  
**Mode:** Alpaca Paper Trading  
**Test Suite:** 18/18 passed

---

## Section 1: Dashboard Visual Audit

**Status: WORKING — with minor data pollution**

The dashboard is a 3-page static HTML SPA served by Flask (dashboard_server.py). All three pages load correctly:

- **Overview Page:** Clean layout. All 10 ETFs displayed. KPI cards (equity, daily P&L, regime, positions) render correctly. Daily P&L tracker section functional. Last Refresh timestamp updates with desk cycles.
- **Alpaca Page:** Account info, positions table, and order history render correctly. Recent Orders section shows old non-ETF symbols (TSLA, AAPL, NVDA, BAC, JPM) from before the ETF-only switch — this is historical Alpaca data, not a bug.
- **Claude Analysis Page:** Active Universe table shows all 10 ETFs correctly. Strategy Performance table's "Best ETF" column shows stale pre-ETF symbols (AAPL, SOL/USD, AVAX/USD, UNH, BRK.B, COST) — data pollution from seed_data.py's historical placeholder data.

**Findings:**
1. ✅ All 3 pages load without JS errors
2. ⚠️ Strategy Performance "Best ETF" data is stale (cosmetic, from seed data)
3. ⚠️ Old non-ETF orders visible in Alpaca history (normal — historical data)
4. ⚠️ Some positions show no SL/TP set (dashes) — only positions with risk book entries show them

---

## Section 2: Data Accuracy

**Status: WORKING — one timezone inconsistency needs attention**

- Conservative mode status returns correct structure with all fields populated
- Fee calculations verified programmatically: $10k notional → $1.00 fee (1 bps), round-trip on $5k → $1.00 total
- Bot registry generates exactly 100 bots (10 ETFs × 10 strategies × 1 timeframe)
- Risk manager status returns correct structure

**Findings:**
1. ✅ Fee model correctly calculates 1 bps for stocks/ETFs
2. ✅ Conservative mode initializes with correct thresholds (+1% profit / -0.5% loss)
3. ✅ Bot registry generates exactly 100 bots as expected
4. ⚠️ **TIMEZONE INCONSISTENCY (RISKY):** DailyLossGuard and TradeFrequencyLimiter in risk_manager.py reset at midnight UTC. Conservative mode resets at midnight ET. This means risk controls could reset 4-5 hours before P&L protection does (or vice versa), creating a window where the two systems disagree on "today."

---

## Section 3: Active ETF Universe

**Status: WORKING 100%**

Config defines exactly 10 ETFs: QQQ, SPY, SOXL, IWM, TQQQ, SOXX, SMH, RSP, SOXS, VOO.

- CRYPTO_ASSETS = [] (empty, disabled)
- MAX_CONCURRENT_CRYPTO = 0
- LEVERAGED_ETFS = {SOXL, TQQQ, SOXS} — correctly identified
- Bot registry confirms all bots are ETF-only
- All 10 ETFs appear in dashboard Active Universe table

**Findings:**
1. ✅ Exactly 10 ETFs configured, no crypto
2. ✅ Leveraged ETFs correctly identified for 50% position sizing
3. ✅ Bot registry produces only ETF bots
4. ✅ Test suite confirms (test #1, #2, #3)

---

## Section 4: Trading Logic (CEO → Manager → Bot → Risk → Execute)

**Status: WORKING — pipeline is sound**

The full pipeline flows: MarketCEO analyzes market → AssetManagers scan bots → signals scored (0-100) → ConservativeMode gates by quality threshold → RiskManager sizes and approves → TradeExecutor submits to Alpaca.

- **MarketCEO:** Analyzes config.STOCK_ASSETS[:20] for regime detection. Score-based direction (bull_score/bear_score). Produces strategy instructions (preferred/avoid lists).
- **AssetManager:** Closed candle rule (_drop_incomplete_candle) prevents acting on forming candles. Duplicate signal prevention per symbol+strategy+candle. 6-component quality score (regime alignment, HTF confirmation, risk:reward, volume, extension, strategy performance).
- **RiskManager:** Risk-based position sizing from conservative_mode.RISK_PER_TRADE_PCT (0.15%). Leveraged ETFs get 50% size. Min order $5.00. Exposure limits: 12% single, 6% leveraged, 90% total.
- **ExitManager:** Stop loss, take profit, partial profit (50%), max hold (96h), trailing stop (ratchet up only), break-even after partial, regime-flip exit, signal invalidation (EMA50).

**Findings:**
1. ✅ Pipeline architecture is correct and well-structured
2. ✅ Closed candle rule prevents stale signal entries
3. ✅ Duplicate signal prevention working
4. ✅ Quality score has 6 meaningful components
5. ✅ Leveraged ETF 50% position sizing enforced
6. ✅ REGIME_COMPATIBLE_TAGS hard-blocks incompatible regime-strategy pairs

---

## Section 5: Timeframes

**Status: WORKING — with stale config**

- Active timeframe: 1H only (DESK_ENTRY_TIMEFRAMES = ["1h"])
- DESK_CYCLE_INTERVAL_MIN = 15 (auto-trader runs every 15 min)
- Legacy 15m/30m intraday cycles gated behind ENABLE_LEGACY_INTRADAY=false (disabled)

**Findings:**
1. ✅ Only 1H timeframe active as intended
2. ✅ Legacy intraday properly disabled via feature flag
3. ⚠️ config.py still has INTRADAY_TIMEFRAMES = ["15m", "30m"] (stale, unused but harmless)
4. ✅ 15-min cycle interval correct for 1H candle trading

---

## Section 6: Daily P&L Protection

**Status: WORKING 100%**

Conservative mode implements 3 daily modes:
- **SAFE_TEST_MODE:** Default. Quality threshold 75+. Normal trading.
- **PROFIT_PROTECTION_MODE:** Triggers at +1% daily equity. Quality threshold 90+. Only high-conviction trades.
- **LOSS_RECOVERY_PROTECTION_MODE:** Triggers at -0.5% daily equity. Quality threshold 90+. Restricts to best setups only.

Dynamic thresholds are calculated from live equity (not hardcoded dollar amounts). Resets daily at midnight ET.

**Findings:**
1. ✅ Three-mode system correctly implemented
2. ✅ Profit threshold: +1.00% of equity
3. ✅ Loss threshold: -0.50% of equity
4. ✅ Dynamic equity-based thresholds (not static dollars)
5. ✅ ET timezone for daily reset
6. ✅ Tracks trades today, wins, losses, win rate, open risk slots

---

## Section 7: Fee / Net P&L Model

**Status: WORKING 100%**

- ETF/Stock fee: EST_FEE_PCT_STOCK = 0.01% (1 bps per side) — models slippage since Alpaca has zero commission
- Crypto fee: EST_FEE_PCT_CRYPTO = 0.20% (20 bps) — unused since crypto is disabled
- trade_journal.py: ALPACA_STOCK_SLIPPAGE_BPS = 1.0, configurable via env var
- Fee calculation verified: $10k notional → $1.00 fee; round-trip $5k → $1.00 total
- Trade ledger CSV records: entry_fee, exit_fee, total_fees, gross_pl, net_pl, net_pl_pct, fee_drag_pct

**Findings:**
1. ✅ Fee model correctly applies 1 bps for ETFs
2. ✅ Round-trip fee math verified programmatically
3. ✅ Trade ledger records both gross and net P&L
4. ✅ Fee drag percentage tracked per trade
5. ✅ Conservative mode subtracts estimated fees from daily P&L tracking
6. ✅ Test suite confirms (test #4, #5, #12)

---

## Section 8: Risk Management

**Status: WORKING — timezone issue flagged**

Risk controls in place:
- **DrawdownCircuitBreaker:** 15% max drawdown from peak equity. Emergency shutdown + close all positions.
- **DailyLossGuard:** 5% max daily loss. Blocks new trades.
- **PositionStopLoss:** 8% per-position stop loss.
- **ExposureLimits:** 12% single position, 6% leveraged ETF, 90% total exposure.
- **TradeFrequencyLimiter:** 10 trades/day max, 2 per symbol/day.
- **DuplicateOrderGuard:** 720 seconds (12 min) minimum between same-symbol orders.
- **CooldownManager:** Reduces exposure multiplier after consecutive losses.
- **StrategyDisabler:** Disables strategies with 3+ consecutive stop-loss exits in 7 days.

**Findings:**
1. ✅ All 8 risk controls implemented and functional
2. ✅ Circuit breaker has emergency shutdown capability
3. ✅ Leveraged ETF exposure correctly limited to 6%
4. ✅ Position sizing uses risk-per-trade from conservative mode (0.15%)
5. ⚠️ **RISKY: DailyLossGuard resets at midnight UTC, not ET.** This is 5 hours ahead of conservative mode's ET reset. Between 7 PM and midnight ET, the guard has already reset to a new day while conservative mode still tracks the previous day. Could allow trades that should be blocked.
6. ⚠️ **RISKY: TradeFrequencyLimiter also resets at midnight UTC.** Same timezone mismatch. Trade count resets 5 hours early.

---

## Section 9: Alpaca Integration

**Status: WORKING 100%**

- Paper trading only (paper=True hardcoded in AlpacaPaperClient)
- Market hours: 9:30 AM – 4:00 PM ET, Mon-Fri (is_us_market_open)
- Live price sanity check: rejects positions with >5% price discrepancy
- EOD Manager: Sells profitable positions at 3:45-3:59 PM ET, keeps losers for next morning
- EOD writes state file to prevent re-runs same day
- Emergency kill switch available via dashboard API

**Findings:**
1. ✅ Paper trading hardcoded — no accidental live trading possible
2. ✅ Market hours correctly implemented (9:30 AM – 4:00 PM ET)
3. ✅ EOD profit-taking window correct (3:45-3:59 PM ET, hard cutoff before 4:00 PM)
4. ✅ EOD records P&L in conservative mode and cleans risk book
5. ✅ Price sanity check prevents acting on stale data
6. ✅ Kill switch endpoint exists and works

---

## Section 10: Telegram Notifications

**Status: WORKING 100%**

- send_message(), send_daily_report(), send_alert() all functional
- Auto-splits messages at 4096 char Telegram limit
- Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID env vars
- EOD manager sends profit-lock notifications
- is_configured() check prevents crashes when Telegram not set up

**Findings:**
1. ✅ Message splitting at 4096 chars
2. ✅ Graceful degradation when not configured
3. ✅ EOD notifications include closed symbols and P&L locked

---

## Section 11: Claude Analysis Page

**Status: PARTIALLY WORKING — stale seed data**

The Claude Analysis page renders correctly with:
- Active Universe table (10 ETFs)
- Strategy Performance table (10 strategies)
- Conservative mode status
- Risk status

**Findings:**
1. ✅ Page layout and structure correct
2. ✅ Active Universe shows all 10 ETFs
3. ⚠️ Strategy Performance "Best ETF" column shows old symbols from seed_data.py (AAPL, SOL/USD, etc.) — these are placeholder values from before the ETF switch, not live data
4. ⚠️ Strategy scores based on seed data, not enough real paper trades yet to produce meaningful scores

---

## Section 12: Code Quality

**Status: GOOD — stale references and dead code identified**

All 32 active Python files pass syntax check. The codebase is well-structured with clear separation of concerns.

**Stale References (cosmetic, not functional bugs):**
1. ⚠️ risk_manager.py line 1 docstring: "crypto trading system" — should say "ETF trading system"
2. ⚠️ conservative_mode.py line 127 comment: "With 100 stocks + 3 crypto" — should say "10 ETFs"
3. ⚠️ config.py line 17: BINANCE_BASE_URL defined but only used by api_client.py/intraday_engine.py (legacy paths)
4. ⚠️ config.py line 91: INTRADAY_TIMEFRAMES = ["15m", "30m"] — unused since legacy intraday disabled

**Dead Code Paths:**
5. ⚠️ dashboard_server.py lines 440-478: /api/alpaca/auto/preview and /api/alpaca/auto/execute routes import old AlpacaTrader class instead of the active AlpacaAutoTrader. These routes still technically work but use the wrong execution path.
6. ⚠️ alpaca_trader.py: Contains old AlpacaTrader class, only referenced by the dead dashboard routes above
7. ⚠️ api_client.py: Imports BINANCE_BASE_URL, creates Binance API client — unused in ETF-only mode
8. ⚠️ intraday_engine.py: Contains Binance candle functions — dead code since legacy intraday disabled

**Crypto References (126 total across 20 files):** Most are in conditional branches, fee model config, or archived files. None are actively executed in the ETF-only path.

---

## Section 13: Testing

**Status: WORKING 100%**

test_etf_only.py passes all 18 tests:
1. ✅ Config has exactly 10 ETFs
2. ✅ No crypto assets configured
3. ✅ Bot registry has only ETF bots
4. ✅ Stock fee is 1 bps
5. ✅ Fee calculation: $10k → $1.00
6. ✅ Conservative mode initializes
7. ✅ No old AlpacaTrader import in dashboard_server.py (top-level)
8. ✅ No imports of archived modules in active code
9. ✅ Leveraged ETFs set is {SOXL, TQQQ, SOXS}
10. ✅ MAX_CONCURRENT_CRYPTO is 0
11. ✅ All Python files pass syntax check
12. ✅ Round-trip fee calculation correct
13. ✅ daily_runner.py exists
14. ✅ No /api/broker routes
15. ✅ No /api/auto/ routes (old AutoTrader)
16. ✅ .env.example clean
17. ✅ archive/ directory exists
18. ✅ Key files archived correctly

---

## Summary

### 1. What is working 100% correctly

- **ETF Universe:** Exactly 10 ETFs, no crypto, leveraged ETFs identified
- **Trading Pipeline:** CEO → Manager → Bot → Risk → Execute flows correctly
- **Conservative Mode:** 3-mode daily P&L protection with dynamic equity thresholds
- **Fee Model:** 1 bps per side for ETFs, correctly applied everywhere
- **EOD Manager:** Profit-taking at 3:45 PM ET, keeps losers, Telegram notifications
- **Alpaca Integration:** Paper-only, market hours, price sanity checks, kill switch
- **Telegram:** Messages, alerts, auto-splitting, graceful degradation
- **Bot Registry:** 100 bots (10 × 10 × 1) correctly generated
- **Exit Manager:** Stop loss, take profit, partial, trailing, regime-flip, max hold
- **Risk Controls:** All 8 controls (circuit breaker, daily loss, exposure, frequency, duplicate, cooldown, strategy disabler, position stop) functional
- **Test Suite:** 18/18 pass
- **Dashboard:** All 3 pages load and render

### 2. What is partially working

- **Claude Analysis Strategy Performance:** Page works but "Best ETF" column shows pre-ETF seed data (AAPL, SOL/USD, etc.) — will self-correct as real trades accumulate
- **Alpaca Order History:** Shows old non-ETF symbols from before the ETF switch — historical data, will age out naturally

### 3. What is broken

- **Nothing is broken.** All core systems are functional. No crashes, no incorrect trades, no data loss.

### 4. What is risky

- **TIMEZONE MISMATCH (MEDIUM RISK):** DailyLossGuard and TradeFrequencyLimiter reset at midnight UTC while conservative_mode resets at midnight ET. This creates a 4-5 hour window where risk controls have reset to a new "day" but P&L protection still tracks the previous day. In practice: between 7 PM and midnight ET, trade frequency counter is already at 0 and daily loss guard has a fresh baseline, but conservative mode still enforces the previous day's P&L-based restrictions. **Impact: Could allow 1-2 extra trades during evening hours that should have been blocked by frequency limits. P&L protection still blocks via conservative mode, so financial risk is limited.**
- **Dead Dashboard Routes (LOW RISK):** /api/alpaca/auto/preview and /api/alpaca/auto/execute use old AlpacaTrader. If someone hits these endpoints, they'd run the old execution path instead of the desk pipeline. Low risk because they require authentication and aren't linked in the dashboard UI.

### 5. What needs fixing before trusting the system

- **Fix timezone mismatch in risk_manager.py:** Change `_utcnow()` and `_today_str()` to use ET timezone (copy the `_et_now()` pattern from conservative_mode.py or eod_manager.py). This is the single most important fix — it ensures all daily resets happen at the same time.

### 6. What can be improved later but is not urgent

- **Remove dead code paths:** Delete /api/alpaca/auto/preview and /api/alpaca/auto/execute routes in dashboard_server.py
- **Update stale comments:** Fix docstrings/comments that still reference "crypto trading system" or "100 stocks + 3 crypto"
- **Clean stale config:** Remove BINANCE_BASE_URL, INTRADAY_TIMEFRAMES from config.py
- **Purge seed data:** Reset seed_data.py placeholder performance data to show ETF symbols instead of old stock/crypto symbols
- **Remove legacy files:** alpaca_trader.py, api_client.py, intraday_engine.py are only used by disabled/dead code paths — archive them
- **Add more tests:** Test conservative mode transitions, EOD behavior, risk manager edge cases

---

**Bottom line:** The system is sound. The trading pipeline, risk controls, P&L protection, and fee model all work correctly. The only item that needs fixing before trusting the system is the timezone mismatch in risk_manager.py. Everything else is cosmetic cleanup that can be done at your pace.
