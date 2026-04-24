"""
Strategy Factory Bot Manager — Dashboard Generator (v3.0)
Complete rewrite: all charts use real data, navigation works, no JS errors.
"""

import json
import os
import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

from config import DASHBOARD_OUTPUT, REPORT_DIR


def _now_est_label():
    """Return a human label like 'Apr 14, 2026 10:02 AM EDT' in US Eastern time."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if ZoneInfo is not None:
        ny = now.astimezone(ZoneInfo("America/New_York"))
        return ny.strftime("%b %d, %Y %I:%M %p %Z")
    return now.strftime("%Y-%m-%d %H:%M UTC")

try:
    from portfolio_allocator import allocate_portfolio
except ImportError:
    allocate_portfolio = None


class DashboardGenerator:
    """Generates a complete single-file HTML dashboard with 8-page sidebar navigation."""

    def __init__(self):
        self.pages = [
            ("overview", "Overview", "📊"),
            ("portfolio", "Portfolio", "💼"),
            ("alpaca-live", "Alpaca", "🔗"),
            ("quantum", "Strategy Scorecard", "⚛️"),
            ("bots", "Bot Signals", "📡"),
            ("performance", "Performance", "📈"),
            ("learning", "Learning Engine", "🧠"),
            ("regime", "Market Regime", "🌊"),
            ("decisions", "Decision Log", "📋"),
        ]

    def generate(self, bots_data, evaluations, regime_info, learning_stats, execution_summary, portfolio=None):
        """Build full HTML dashboard from real data."""
        # Normalize evaluations: if list, convert to dict keyed by bot_name
        if isinstance(evaluations, list):
            eval_dict = {}
            for ev in evaluations:
                name = ev.get("bot_name", ev.get("name", f"Bot-{ev.get('bot_id', '?')}"))
                m = ev.get("metrics", {})
                eval_dict[name] = {
                    "verdict": ev.get("enhanced_verdict", ev.get("verdict", "HOLD")),
                    "base_verdict": ev.get("verdict", "HOLD"),
                    "win_rate": m.get("win_rate", 0),
                    "profit_factor": m.get("profit_factor", 0),
                    "sharpe_ratio": m.get("sharpe_ratio", 0),
                    "max_drawdown": m.get("max_drawdown", 0),
                    "net_profit": m.get("net_profit", 0),
                    "total_trades": m.get("total_trades", 0),
                    "avg_win": m.get("avg_win", 0),
                    "avg_loss": m.get("avg_loss", 0),
                    "consecutive_losses": m.get("consecutive_losses", 0),
                    "adaptation_score": ev.get("adaptation_score", 50),
                    "adaptation_label": ev.get("adaptation_label", "NEUTRAL"),
                    "pair": ev.get("pair", ""),
                    "strategy_type": ev.get("strategy_type", ""),
                    "bot_status": ev.get("bot_status", "active"),
                    "reasons": ev.get("reasons", []),
                }
            evaluations = eval_dict

        # Normalize learning_stats — compute realistic scores from bot metrics
        if isinstance(learning_stats, dict) and "calibration" in learning_stats and len(learning_stats) <= 2:
            cal = learning_stats.get("calibration", {})
            new_ls = {}
            for name, ev in evaluations.items():
                # If a real adaptation_score exists and isn't the default 50, use it
                existing = ev.get("adaptation_score", 0)
                if existing and existing != 50:
                    score = existing
                else:
                    # Compute a realistic score from the bot's actual metrics
                    score = 50  # base
                    wr = self._num(ev.get("win_rate", 50))
                    pf = self._num(ev.get("profit_factor", 1.0))
                    sharpe = self._num(ev.get("sharpe_ratio", 0))
                    trades = self._num(ev.get("total_trades", 0))
                    mf = self._num(ev.get("market_fit", 50))

                    # Win rate contribution (-15 to +20)
                    if wr >= 70: score += 20
                    elif wr >= 60: score += 12
                    elif wr >= 50: score += 5
                    elif wr >= 40: score -= 5
                    else: score -= 15

                    # Profit factor contribution (-15 to +15)
                    if pf >= 2.5: score += 15
                    elif pf >= 1.8: score += 10
                    elif pf >= 1.2: score += 3
                    elif pf < 0.9: score -= 15
                    elif pf < 1.0: score -= 8

                    # Sharpe ratio contribution (-10 to +10)
                    if sharpe >= 2.0: score += 10
                    elif sharpe >= 1.0: score += 5
                    elif sharpe < 0: score -= 10

                    # Trade volume bonus (more trades = more confidence)
                    if trades >= 100: score += 5
                    elif trades < 10: score -= 10

                    # Market fit bonus
                    if mf >= 80: score += 5
                    elif mf < 40: score -= 5

                    score = max(0, min(100, score))

                if score >= 75: label = "WELL_ADAPTED"
                elif score >= 55: label = "MODERATELY_ADAPTED"
                elif score >= 40: label = "NEUTRAL"
                elif score >= 25: label = "POORLY_ADAPTED"
                else: label = "MISMATCHED"

                new_ls[name] = {
                    "adaptation_score": score,
                    "adaptation_label": label,
                }
            new_ls["_calibration"] = cal
            learning_stats = new_ls

        ts = _now_est_label()
        parts = [self._head(ts)]
        parts.append(self._sidebar())
        parts.append('<div class="main-content">')
        parts.append(self._page_overview(bots_data, evaluations, regime_info, execution_summary, ts))
        parts.append(self._page_portfolio(evaluations, portfolio))
        parts.append(self._page_alpaca_live())
        parts.append(self._page_quantum(evaluations))
        parts.append(self._page_bots(bots_data, evaluations))
        parts.append(self._page_performance(evaluations))
        parts.append(self._page_learning(learning_stats, evaluations))
        parts.append(self._page_regime(regime_info))
        parts.append(self._page_decisions(evaluations))
        parts.append("</div>")
        parts.append(self._scripts(evaluations, portfolio, regime_info))
        parts.append("</body></html>")
        return "\n".join(parts)

    def generate_mock(self):
        """Generate dashboard with realistic sample data for preview."""
        bots, evals, regime, learning, summary, portfolio = self._mock_data()
        return self.generate(bots, evals, regime, learning, summary, portfolio)

    def save(self, html_content, output_path=None):
        path = output_path or DASHBOARD_OUTPUT
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_content)
        return path

    def _num(self, value, default=0.0):
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _fmt_metric(self, value, decimals=2, suffix=""):
        if value is None or value == "":
            return "—"
        num = self._num(value, None)
        if num is None:
            return "—"
        return f"{num:.{decimals}f}{suffix}"

    def _quality_badge(self, metric, value):
        """Return a small colored badge like (Good) or (Weak) for a metric value."""
        v = self._num(value, 0)
        if metric == "pf":  # profit factor
            if v >= 1.5: return '<span style="color:var(--lime);font-size:0.75em;"> ● Strong</span>'
            if v >= 1.0: return '<span style="color:var(--amber);font-size:0.75em;"> ● OK</span>'
            return '<span style="color:var(--red);font-size:0.75em;"> ● Weak</span>'
        if metric == "sharpe":
            if v >= 1.0: return '<span style="color:var(--lime);font-size:0.75em;"> ● Strong</span>'
            if v >= 0.3: return '<span style="color:var(--amber);font-size:0.75em;"> ● OK</span>'
            return '<span style="color:var(--red);font-size:0.75em;"> ● Weak</span>'
        if metric == "wr":  # win rate
            if v >= 60: return '<span style="color:var(--lime);font-size:0.75em;"> ● Strong</span>'
            if v >= 45: return '<span style="color:var(--amber);font-size:0.75em;"> ● OK</span>'
            return '<span style="color:var(--red);font-size:0.75em;"> ● Weak</span>'
        if metric == "adapt":
            if v >= 75: return '<span style="color:var(--lime);font-size:0.75em;"> ● Strong</span>'
            if v >= 50: return '<span style="color:var(--amber);font-size:0.75em;"> ● OK</span>'
            return '<span style="color:var(--red);font-size:0.75em;"> ● Weak</span>'
        if metric == "dd":  # max drawdown (negative, lower abs is better)
            av = abs(v)
            if av <= 10: return '<span style="color:var(--lime);font-size:0.75em;"> ● Low</span>'
            if av <= 25: return '<span style="color:var(--amber);font-size:0.75em;"> ● Moderate</span>'
            return '<span style="color:var(--red);font-size:0.75em;"> ● High</span>'
        return ''

    def _evaluation_for_bot(self, bot, evaluations):
        name = bot.get("name", "")
        bot_id = bot.get("id")

        if name in evaluations:
            return evaluations[name]

        if bot_id is not None:
            bot_id_str = str(bot_id)
            for entry in evaluations.values():
                if str(entry.get("bot_id", "")) == bot_id_str:
                    return entry

        return {}

    # ── CSS ──────────────────────────────────────────────────────────────
    def _head(self, timestamp):
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Strategy Factory — Command Center</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box;}}
:root{{
  --bg:#0a0e27;--card:#1a1f3a;--card-hover:#222850;--border:#2d3561;
  --cyan:#00d4ff;--lime:#39ff14;--amber:#ffb700;--magenta:#ff006e;
  --red:#ff4444;--green:#00dd77;--gray:#6b7394;--text:#e0e6ff;--text-dim:#8892b0;
  --panel:#151a3a;--panel-soft:rgba(0,212,255,0.045);
}}
html,body{{height:100%;}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);line-height:1.6;overflow-x:hidden;display:flex;min-height:100vh;font-variant-numeric:tabular-nums;}}

/* SIDEBAR */
.sidebar{{width:260px;min-height:100vh;background:linear-gradient(180deg,#0d1130 0%,#151a3a 100%);border-right:1px solid var(--border);position:fixed;top:0;left:0;z-index:100;display:flex;flex-direction:column;box-shadow:4px 0 20px rgba(0,0,0,0.4);}}
.sidebar-header{{padding:28px 24px;border-bottom:1px solid var(--border);background:linear-gradient(135deg,rgba(0,212,255,0.05) 0%,rgba(57,255,20,0.05) 100%);}}
.sidebar-header h1{{font-size:1.15em;font-weight:700;background:linear-gradient(90deg,var(--cyan),var(--lime));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;letter-spacing:-0.5px;}}
.sidebar-header p{{font-size:0.72em;color:var(--text-dim);margin-top:6px;font-weight:500;text-transform:uppercase;letter-spacing:0.5px;}}
.sidebar-nav{{flex:1;padding:16px 0;overflow-y:auto;}}
.nav-item{{display:flex;align-items:center;padding:14px 20px;cursor:pointer;transition:all 0.25s cubic-bezier(0.4,0,0.2,1);color:var(--text-dim);text-decoration:none;border-left:3px solid transparent;font-size:0.95em;font-weight:500;}}
.nav-item:hover{{background:rgba(0,212,255,0.06);color:var(--cyan);}}
.nav-item.active{{background:rgba(0,212,255,0.12);color:var(--cyan);border-left-color:var(--cyan);font-weight:600;}}
.nav-item span.icon{{font-size:1.3em;margin-right:14px;width:24px;text-align:center;}}
.sidebar-footer{{padding:16px 20px;border-top:1px solid var(--border);font-size:0.7em;color:var(--text-dim);text-align:center;line-height:1.4;}}

/* MAIN */
.main-content{{margin-left:260px;flex:1;padding:96px 40px 32px;min-height:100vh;min-width:0;max-width:100%;}}
.page{{display:none;animation:fadeIn 0.4s ease;}}
.page.active{{display:block;}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(10px);}}to{{opacity:1;transform:translateY(0);}}}}
@keyframes pulse{{0%,100%{{opacity:1;}}50%{{opacity:0.5;}}}}

.page-title{{font-size:2em;font-weight:700;margin-bottom:22px;display:flex;align-items:center;gap:14px;color:var(--text);letter-spacing:0;flex-wrap:wrap;min-width:0;}}
.page-title .accent{{color:var(--cyan);}}
.data-badge{{font-size:0.5em;background:rgba(255,170,0,0.15);color:var(--amber);padding:3px 10px;border-radius:8px;vertical-align:middle;line-height:1.35;white-space:normal;max-width:100%;}}
.page-sub{{color:var(--text-dim);margin-top:-12px;margin-bottom:24px;max-width:820px;}}
.section-card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:24px;margin-bottom:24px;overflow:hidden;}}
.section-header{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:16px;}}
.section-title{{font-size:1.08em;font-weight:700;color:var(--text);}}
.section-sub{{font-size:0.86em;color:var(--text-dim);margin-top:3px;line-height:1.45;}}
.status-pill{{display:inline-flex;align-items:center;gap:8px;padding:7px 12px;border:1px solid var(--border);border-radius:999px;background:rgba(10,14,39,0.42);font-size:0.82em;font-weight:700;white-space:nowrap;}}
.status-pill.ok{{color:var(--lime);border-color:rgba(57,255,20,0.45);background:rgba(57,255,20,0.08);}}
.status-pill.warn{{color:var(--amber);border-color:rgba(255,183,0,0.45);background:rgba(255,183,0,0.08);}}
.status-pill.danger{{color:var(--red);border-color:rgba(255,68,68,0.45);background:rgba(255,68,68,0.08);}}
.metric-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:22px;}}
.metric-card{{background:rgba(10,14,39,0.42);border:1px solid var(--border);border-radius:8px;padding:18px;min-width:0;}}
.metric-label{{font-size:0.78em;color:var(--text-dim);font-weight:700;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:8px;}}
.metric-value{{font-size:2em;font-weight:750;font-family:'Courier New',monospace;color:var(--cyan);line-height:1.12;letter-spacing:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.metric-sub{{font-size:0.82em;color:var(--text-dim);margin-top:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.num{{font-family:'Courier New',monospace;font-variant-numeric:tabular-nums;letter-spacing:0;text-align:right;white-space:nowrap;}}
.text-right{{text-align:right;}}
.muted-line{{display:block;color:var(--text-dim);font-size:0.78em;margin-top:2px;line-height:1.35;}}
.read-only-note{{border:1px solid rgba(0,212,255,0.28);background:rgba(0,212,255,0.055);border-radius:8px;padding:12px 14px;color:var(--text-dim);font-size:0.9em;line-height:1.45;}}

/* TOOLTIP */
.tip{{position:relative;cursor:help;border-bottom:1px dotted var(--text-dim);}}
.tip:hover::after{{
  content:attr(data-tip);position:absolute;bottom:100%;left:50%;transform:translateX(-50%);
  background:#0d1130;color:var(--cyan);padding:8px 12px;border-radius:6px;font-size:0.75em;
  white-space:nowrap;border:1px solid var(--border);z-index:1000;margin-bottom:8px;font-weight:500;
}}

/* CARDS */
.cards-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:20px;margin-bottom:28px;}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:24px;transition:border-color 0.2s,box-shadow 0.2s;position:relative;overflow:hidden;}}
.card:hover{{border-color:rgba(0,212,255,0.65);box-shadow:0 8px 24px rgba(0,212,255,0.08);}}
.card-label{{font-size:0.75em;color:var(--text-dim);text-transform:uppercase;letter-spacing:1.2px;margin-bottom:10px;font-weight:600;}}
.card-value{{font-size:2.2em;font-weight:700;font-family:'Courier New',monospace;color:var(--cyan);letter-spacing:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;font-variant-numeric:tabular-nums;}}
.card-sub{{font-size:0.85em;color:var(--text-dim);margin-top:8px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}}

/* TABLE */
.table-wrap{{width:100%;max-width:100%;overflow-x:auto;border-radius:8px;background:rgba(10,14,39,0.22);}}
.data-table{{width:100%;border-collapse:collapse;font-size:0.9em;table-layout:auto;}}
.data-table.compact{{font-size:0.84em;}}
.data-table th{{background:var(--panel);color:var(--cyan);padding:12px 16px;text-align:left;font-weight:700;border-bottom:1px solid var(--cyan);cursor:pointer;user-select:none;white-space:nowrap;font-size:0.86em;letter-spacing:0.2px;}}
.data-table th:hover{{color:var(--lime);background:#1a2250;}}
.data-table td{{padding:12px 16px;border-bottom:1px solid var(--border);vertical-align:top;}}
.data-table tr:hover td{{background:rgba(0,212,255,0.035);}}
.data-table th.num,.data-table td.num{{text-align:right;}}
.trade-monitor-table th,.trade-monitor-table td{{padding:10px 12px;vertical-align:top;}}
.trade-monitor-table .price-cell{{font-family:'Courier New',monospace;font-weight:700;color:var(--text);white-space:nowrap;}}
.trade-monitor-table .risk-cell{{font-family:'Courier New',monospace;line-height:1.45;white-space:nowrap;}}
.trade-monitor-table .reason-row td{{padding-top:0;color:var(--text-dim);font-size:0.82em;line-height:1.45;background:rgba(13,17,48,0.32);}}
.trade-monitor-table .reason-row strong{{color:var(--cyan);font-weight:600;}}
.live-dot{{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--lime);box-shadow:0 0 10px rgba(57,255,20,0.9);animation:pulse 1.2s infinite;margin-right:6px;}}
.nowrap{{white-space:nowrap;}}
.data-table .row-pause{{border-left:3px solid var(--red);}}
.data-table .row-hold{{border-left:3px solid var(--amber);}}
.data-table .row-reactivate{{border-left:3px solid var(--lime);}}
.data-table .row-insufficient_data{{border-left:3px solid var(--gray);}}

/* BADGES */
.badge{{display:inline-block;padding:4px 12px;border-radius:20px;font-size:0.75em;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;}}
.badge-active{{background:rgba(57,255,20,0.15);color:var(--lime);border:1px solid rgba(57,255,20,0.4);}}
.badge-paused{{background:rgba(255,183,0,0.15);color:var(--amber);border:1px solid rgba(255,183,0,0.4);}}
.badge-pause{{background:rgba(255,68,68,0.15);color:var(--red);border:1px solid rgba(255,68,68,0.4);}}
.badge-hold{{background:rgba(255,183,0,0.15);color:var(--amber);border:1px solid rgba(255,183,0,0.4);}}
.badge-reactivate{{background:rgba(57,255,20,0.15);color:var(--lime);border:1px solid rgba(57,255,20,0.4);}}
.badge-insufficient_data{{background:rgba(107,115,148,0.15);color:var(--gray);border:1px solid rgba(107,115,148,0.4);}}

/* BOT GRID */
.bot-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:20px;}}
.bot-card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:22px;transition:all 0.3s;}}
.bot-card:hover{{border-color:var(--cyan);transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,212,255,0.1);}}
.bot-card-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px;}}
.bot-card-name{{font-weight:700;font-size:1.05em;}}
.bot-card-pair{{font-size:0.8em;color:var(--cyan);font-family:'Courier New',monospace;font-weight:600;}}
.bot-card-metrics{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:14px 0;font-size:0.85em;}}
.bot-card-metric-label{{color:var(--text-dim);font-weight:500;}}
.bot-card-metric-val{{font-family:'Courier New',monospace;font-weight:700;color:var(--cyan);}}
.status-pulse{{width:10px;height:10px;border-radius:50%;background:var(--lime);animation:pulse 2s infinite;display:inline-block;margin-left:6px;}}

/* CHART */
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:28px;}}
.chart-box{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:24px;}}
.chart-box h3{{font-size:1em;color:var(--text);margin-bottom:14px;font-weight:600;}}
.chart-box canvas{{width:100%!important;height:280px!important;max-height:320px;}}
.chart-fallback{{display:flex;flex-direction:column;gap:10px;padding:4px 0;min-height:220px;justify-content:center;}}
.chart-fallback-note{{color:var(--text-dim);font-size:0.82em;margin-bottom:6px;}}
.chart-fallback-row{{display:flex;justify-content:space-between;gap:16px;padding:10px 12px;background:rgba(0,212,255,0.05);border:1px solid rgba(45,53,97,0.8);border-radius:10px;font-size:0.88em;}}
.chart-fallback-row strong{{color:var(--text);font-weight:600;}}
.chart-fallback-row span{{color:var(--cyan);font-family:'Courier New',monospace;}}
@media(max-width:1400px){{.chart-grid{{grid-template-columns:1fr;}}}}

/* PORTFOLIO */
.portfolio-hero{{background:linear-gradient(135deg,rgba(0,212,255,0.1) 0%,rgba(57,255,20,0.1) 100%);border:1px solid var(--border);border-radius:8px;padding:40px;margin-bottom:32px;text-align:center;}}
.portfolio-hero-value{{font-size:3.5em;font-weight:700;color:var(--cyan);font-family:'Courier New',monospace;margin:16px 0;}}
.portfolio-hero-subtitle{{font-size:1.1em;color:var(--text-dim);}}
.portfolio-grid{{display:grid;grid-template-columns:2fr 1fr;gap:24px;margin-bottom:32px;}}
.portfolio-excluded{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:24px;margin-bottom:28px;}}
.portfolio-excluded h3{{color:var(--amber);margin-bottom:16px;}}
.excluded-item{{padding:12px;background:rgba(255,183,0,0.05);border-left:3px solid var(--amber);border-radius:6px;margin-bottom:10px;font-size:0.9em;}}
.portfolio-summary{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:24px;margin-bottom:28px;}}
.summary-row{{display:flex;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border);}}
.summary-row:last-child{{border-bottom:none;}}
.summary-label{{color:var(--text-dim);font-weight:500;}}
.summary-value{{font-weight:700;color:var(--cyan);font-family:'Courier New',monospace;}}
.disclaimer{{background:rgba(255,183,0,0.08);border:1px solid var(--border);border-radius:10px;padding:16px;margin-top:28px;font-size:0.85em;color:var(--text-dim);text-align:center;}}

/* REGIME */
.regime-badge-large{{display:inline-flex;align-items:center;gap:14px;padding:18px 32px;border-radius:14px;font-size:1.4em;font-weight:700;border:2px solid;margin-bottom:24px;}}
.regime-trending_up{{background:rgba(0,221,119,0.12);color:var(--green);border-color:var(--green);}}
.regime-trending_down{{background:rgba(255,68,68,0.12);color:var(--red);border-color:var(--red);}}
.regime-mean_reverting{{background:rgba(255,183,0,0.12);color:var(--amber);border-color:var(--amber);}}
.regime-high_volatility{{background:rgba(255,0,110,0.12);color:var(--magenta);border-color:var(--magenta);}}
.regime-low_volatility{{background:rgba(0,212,255,0.12);color:var(--cyan);border-color:var(--cyan);}}
.regime-choppy,.regime-unknown{{background:rgba(107,115,148,0.12);color:var(--gray);border-color:var(--gray);}}

/* ADAPT CARDS */
.adapt-cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px;margin-bottom:28px;}}
.adapt-card{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:20px;text-align:center;}}
.adapt-score{{font-size:2.4em;font-weight:700;font-family:'Courier New',monospace;margin:12px 0;}}
.adapt-label{{font-size:0.75em;margin-top:6px;text-transform:uppercase;letter-spacing:1px;font-weight:600;color:var(--text-dim);}}
.progress-bar{{width:100%;height:6px;background:#151a3a;border-radius:3px;overflow:hidden;}}
.progress-fill{{height:100%;border-radius:3px;transition:width 0.6s;}}
.score-well{{color:var(--lime);}}
.score-moderate{{color:var(--cyan);}}
.score-neutral{{color:var(--amber);}}
.score-poor{{color:var(--red);}}

/* DECISION LOG */
.decision-timeline{{position:relative;padding:20px 0;}}
.decision-item{{background:var(--card);border-left:4px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px;transition:all 0.2s;}}
.decision-item:hover{{border-left-color:var(--cyan);background:#222850;}}
.decision-item.override{{border-left-color:var(--magenta);}}
.decision-meta{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:8px;}}
.decision-bot{{font-weight:700;color:var(--cyan);}}
.decision-reason{{font-size:0.9em;color:var(--text-dim);margin-top:10px;line-height:1.5;}}

/* FILTER */
.filter-buttons{{display:flex;gap:10px;margin-bottom:24px;flex-wrap:wrap;}}
.filter-btn{{padding:8px 16px;background:var(--card);border:1px solid var(--border);border-radius:8px;cursor:pointer;color:var(--text-dim);transition:all 0.2s;font-size:0.9em;font-weight:500;}}
.filter-btn:hover{{border-color:var(--cyan);color:var(--cyan);}}
.filter-btn.active{{background:rgba(0,212,255,0.15);color:var(--cyan);border-color:var(--cyan);}}

/* GANTT TIMELINE */
.gantt-row{{display:flex;align-items:center;margin-bottom:6px;}}
.gantt-label{{width:130px;min-width:130px;font-size:0.8em;font-weight:600;color:var(--text);padding-right:12px;text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.gantt-track{{flex:1;height:28px;background:rgba(13,17,48,0.5);border-radius:6px;position:relative;overflow:hidden;}}
.gantt-bar{{position:absolute;height:100%;border-radius:6px;min-width:4px;cursor:pointer;transition:opacity 0.2s;}}
.gantt-bar:hover{{opacity:0.85;}}
.gantt-bar .gantt-tooltip{{display:none;position:absolute;bottom:110%;left:50%;transform:translateX(-50%);background:#0d1130;color:var(--cyan);padding:8px 12px;border-radius:6px;font-size:0.75em;white-space:nowrap;border:1px solid var(--border);z-index:100;pointer-events:none;}}
.gantt-bar:hover .gantt-tooltip{{display:block;}}
.gantt-axis{{display:flex;margin-left:130px;margin-top:8px;font-size:0.7em;color:var(--text-dim);}}
.gantt-axis span{{flex:1;text-align:center;}}

/* P&L CALENDAR */
.pnl-calendar{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;margin-bottom:28px;}}
.cal-stats-bar{{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:20px;padding:16px 0;border-bottom:1px solid var(--border);}}
.cal-stat-box{{text-align:center;}}
.cal-stat-value{{font-size:1.15em;font-weight:700;font-family:'Courier New',monospace;color:var(--cyan);}}
.cal-stat-label{{font-size:0.68em;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.8px;margin-top:4px;font-weight:600;}}
.pnl-calendar-header{{display:flex;justify-content:center;align-items:center;margin-bottom:18px;}}
.pnl-calendar-nav{{display:flex;align-items:center;gap:16px;}}
.pnl-calendar-nav button{{background:transparent;border:1px solid var(--border);border-radius:8px;padding:8px 16px;cursor:pointer;color:var(--text-dim);font-size:0.9em;font-weight:600;transition:all 0.2s;}}
.pnl-calendar-nav button:hover{{border-color:var(--cyan);color:var(--cyan);}}
.pnl-calendar-nav .cal-month-label{{font-size:1.15em;font-weight:700;color:var(--text);min-width:180px;text-align:center;}}
.pnl-calendar-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:5px;}}
.pnl-cal-dayheader{{text-align:center;font-size:0.72em;color:var(--text-dim);text-transform:uppercase;letter-spacing:1px;padding:8px 0;font-weight:600;}}
.pnl-cal-cell{{background:rgba(13,17,48,0.4);border:1px solid rgba(45,53,97,0.3);border-radius:10px;padding:10px 6px 8px;min-height:76px;text-align:center;transition:all 0.2s;position:relative;}}
.pnl-cal-cell:hover{{border-color:var(--cyan);background:rgba(0,212,255,0.04);transform:translateY(-1px);}}
.pnl-cal-cell.empty{{background:transparent;border-color:transparent;min-height:0;}}
.pnl-cal-cell .cal-day{{font-size:0.72em;color:var(--text-dim);margin-bottom:5px;font-weight:600;}}
.pnl-cal-cell .cal-pnl-usd{{font-size:0.88em;font-weight:700;font-family:'Courier New',monospace;}}
.pnl-cal-cell .cal-pnl-pct{{font-size:0.68em;font-family:'Courier New',monospace;margin-top:2px;opacity:0.8;}}
.pnl-cal-cell .cal-trades{{font-size:0.62em;color:var(--text-dim);margin-top:3px;font-weight:500;}}
.pnl-cal-cell.positive{{background:rgba(57,255,20,0.06);border-color:rgba(57,255,20,0.2);}}
.pnl-cal-cell.positive .cal-pnl-usd{{color:var(--lime);}}
.pnl-cal-cell.positive .cal-pnl-pct{{color:var(--lime);}}
.pnl-cal-cell.negative{{background:rgba(255,68,68,0.06);border-color:rgba(255,68,68,0.2);}}
.pnl-cal-cell.negative .cal-pnl-usd{{color:var(--red);}}
.pnl-cal-cell.negative .cal-pnl-pct{{color:var(--red);}}
.pnl-cal-cell.zero .cal-pnl-usd{{color:var(--text-dim);}}
.pnl-cal-cell.zero .cal-pnl-pct{{color:var(--text-dim);}}
.pnl-cal-cell.today{{border-color:var(--cyan);box-shadow:0 0 10px rgba(0,212,255,0.25);}}
.pnl-cal-summary{{display:flex;gap:24px;margin-top:16px;padding:14px 20px;background:rgba(0,212,255,0.04);border:1px solid rgba(45,53,97,0.6);border-radius:10px;flex-wrap:wrap;}}
.pnl-cal-summary-item{{display:flex;flex-direction:column;gap:2px;}}
.pnl-cal-summary-label{{font-size:0.72em;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;font-weight:600;}}
.pnl-cal-summary-value{{font-size:1em;font-weight:700;font-family:'Courier New',monospace;color:var(--cyan);}}
.pnl-cal-nodata{{text-align:center;padding:40px 20px;color:var(--text-dim);font-size:0.9em;}}

/* FOOTER */
.footer{{text-align:center;padding:32px 0;border-top:1px solid var(--border);margin-top:40px;font-size:0.85em;color:var(--text-dim);}}

/* LAST REFRESH BAR */
.last-refresh-badge{{
  position:fixed;top:18px;left:300px;right:40px;z-index:90;
  background:rgba(13,17,48,0.92);backdrop-filter:blur(12px);
  border:1px solid var(--border);border-radius:8px;
  padding:10px 16px;font-size:0.8em;color:var(--text-dim);
  box-shadow:0 10px 30px rgba(0,0,0,0.22);display:flex;align-items:center;justify-content:flex-end;gap:10px;
  transition:border-color 0.3s;
}}
.last-refresh-badge:hover{{border-color:var(--cyan);}}
.last-refresh-badge .lr-dot{{
  width:8px;height:8px;border-radius:50%;background:var(--gray);animation:pulse 3s infinite;
}}
.last-refresh-badge.fresh .lr-dot{{background:var(--lime);}}
.last-refresh-badge.stale .lr-dot{{background:var(--amber);}}
.last-refresh-badge.missing .lr-dot{{background:var(--red);animation:none;}}
.last-refresh-badge .lr-label{{font-weight:600;color:var(--text);text-transform:uppercase;letter-spacing:0.5px;font-size:0.72em;}}
.last-refresh-badge .lr-time{{font-family:'Courier New',monospace;color:var(--cyan);}}
.last-refresh-badge .lr-trigger{{font-size:0.7em;padding:2px 8px;border-radius:10px;background:rgba(0,212,255,0.12);color:var(--cyan);text-transform:uppercase;letter-spacing:0.5px;}}
.last-refresh-badge .lr-trigger.manual{{background:rgba(255,183,0,0.12);color:var(--amber);}}
@media(max-width:900px){{.last-refresh-badge{{top:10px;left:80px;right:10px;font-size:0.72em;padding:8px 12px;}}}}

@media(max-width:1200px){{
  .sidebar{{width:70px;}}
  .sidebar-header h1,.sidebar-header p,.nav-item span.label,.sidebar-footer{{display:none;}}
  .nav-item{{justify-content:center;padding:12px;}}
  .nav-item span.icon{{margin:0;}}
  .main-content{{margin-left:70px;padding:88px 20px 20px;}}
  .last-refresh-badge{{left:90px;right:20px;}}
  .chart-grid,.portfolio-grid{{grid-template-columns:1fr;}}
  .bot-grid{{grid-template-columns:1fr;}}
}}
@media(max-width:520px){{
  .page-title{{font-size:1.65em;gap:10px;}}
  .data-badge{{font-size:0.48em;}}
  .cal-stats-bar{{grid-template-columns:repeat(3,1fr);gap:8px;}}
  .pnl-calendar-header{{align-items:center;}}
  .pnl-calendar-nav .cal-month-label{{min-width:140px;font-size:1em;}}
  .pnl-calendar{{padding:16px;}}
  .pnl-calendar-grid{{gap:3px;}}
  .pnl-cal-cell{{min-height:60px;padding:6px 3px;}}
  .pnl-cal-cell .cal-pnl-usd{{font-size:0.78em;}}
  .pnl-cal-cell .cal-pnl-pct{{font-size:0.6em;}}
  .pnl-cal-cell .cal-trades{{font-size:0.58em;}}
}}
</style>
</head>
<body>
<!-- LAST REFRESH BADGE (top right) -->
<div class="last-refresh-badge missing" id="lastRefreshBadge" title="Click to view refresh details">
  <span class="lr-dot"></span>
  <div>
    <div class="lr-label">Last Refresh</div>
    <div class="lr-time" id="lrTime">checking...</div>
  </div>
  <span class="lr-trigger" id="lrTrigger" style="display:none;">—</span>
</div>"""

    # ── SIDEBAR ──────────────────────────────────────────────────────────
    def _sidebar(self):
        items = ""
        for i, (pid, label, icon) in enumerate(self.pages):
            active = " active" if i == 0 else ""
            items += f'<a class="nav-item{active}" data-page="{pid}" onclick="showPage(\'{pid}\')"><span class="icon">{icon}</span><span class="label">{label}</span></a>\n'
        return f"""
<div class="sidebar">
  <div class="sidebar-header">
    <h1>Strategy Factory</h1>
    <p>Strategy Scorecard</p>
  </div>
  <nav class="sidebar-nav">{items}</nav>
  <div class="sidebar-footer">v3.0<br>Adaptive Intelligence</div>
</div>"""

    # ── PAGE: OVERVIEW ───────────────────────────────────────────────────
    def _page_overview(self, bots_data, evaluations, regime_info, execution_summary, ts):
        total = len(bots_data) or len(evaluations)
        active_count = sum(1 for b in bots_data if str(b.get("status", "")).lower() == "active")
        paused_count = sum(1 for b in bots_data if str(b.get("status", "")).lower() == "paused")
        if not active_count and not paused_count:
            active_count = sum(1 for e in evaluations.values() if str(e.get("bot_status", "")).lower() == "active")
            paused_count = sum(1 for e in evaluations.values() if str(e.get("bot_status", "")).lower() == "paused")
        total_pnl = sum(self._num(e.get("net_profit", 0)) for e in evaluations.values())
        avg_wr = sum(self._num(e.get("win_rate", 0)) for e in evaluations.values()) / max(len(evaluations), 1)
        avg_pf = sum(self._num(e.get("profit_factor", 0)) for e in evaluations.values()) / max(len(evaluations), 1)
        regime = regime_info.get("regime", "unknown")
        regime_conf = self._num(regime_info.get("confidence", 0))
        if regime_conf <= 1:
            regime_conf *= 100
        pnl_color = "cyan" if total_pnl >= 0 else "red"
        pnl_arrow = "↗" if total_pnl >= 0 else "↘"

        # Verdict counts
        vc = {}
        for e in evaluations.values():
            v = e.get("verdict", "HOLD").upper()
            vc[v] = vc.get(v, 0) + 1

        return f"""<div class="page active" id="overview">
  <div class="page-title"><span class="accent">📊</span> Overview</div>

  <!-- LIVE ACCOUNT (populated via JS from Alpaca) -->
  <h3 style="color:var(--cyan);margin-bottom:14px;font-size:1em;text-transform:uppercase;letter-spacing:1px;">🦙 Your Alpaca Account</h3>
  <div class="cards-row">
    <div class="card">
      <div class="card-label">Equity</div>
      <div id="ovEquity" class="card-value">$—</div>
      <div class="card-sub">Cash + positions market value</div>
    </div>
    <div class="card">
      <div class="card-label">Today's P&L</div>
      <div id="ovTotalPL" class="card-value">$—</div>
      <div id="ovTotalPLsub" class="card-sub">since last close</div>
    </div>
    <div class="card">
      <div class="card-label">Last Close</div>
      <div id="ovStart" class="card-value">$—</div>
      <div class="card-sub">Previous day equity</div>
    </div>
    <div class="card">
      <div class="card-label">Buying Power</div>
      <div id="ovCash" class="card-value">$—</div>
      <div class="card-sub">Available to trade</div>
    </div>
    <div class="card">
      <div class="card-label">Open Positions</div>
      <div id="ovPositions" class="card-value">—</div>
      <div class="card-sub">Currently held</div>
    </div>
    <div class="card">
      <div class="card-label">Market Regime</div>
      <div class="card-value" style="font-size:1.2em;">{regime.replace('_',' ').title()}</div>
      <div class="card-sub">{regime_conf:.0f}% confidence</div>
    </div>
  </div>

  <!-- POSITIONS SUMMARY (populated via JS from Alpaca) -->
  <h3 style="color:var(--cyan);margin-top:12px;margin-bottom:14px;font-size:1em;text-transform:uppercase;letter-spacing:1px;">📈 Your Positions</h3>
  <div id="ovPositionsTable" style="margin-bottom:20px;">
    <div style="padding:24px;text-align:center;color:var(--text-dim);background:var(--card);border:1px solid var(--border);border-radius:12px;">Loading positions...</div>
  </div>
  <!-- P&L CALENDAR -->
  <div class="pnl-calendar" id="pnlCalendarSection">
    <!-- Month stats bar (like YouTuber screenshot) -->
    <div class="cal-stats-bar" id="calStatsBar" style="display:none;">
      <div class="cal-stat-box">
        <div class="cal-stat-value" id="calStatPnl">$0.00</div>
        <div class="cal-stat-label">Month P&L</div>
      </div>
      <div class="cal-stat-box">
        <div class="cal-stat-value" id="calStatPct">0.00%</div>
        <div class="cal-stat-label">Month %</div>
      </div>
      <div class="cal-stat-box">
        <div class="cal-stat-value" id="calStatTrades">0</div>
        <div class="cal-stat-label">Trades</div>
      </div>
      <div class="cal-stat-box">
        <div class="cal-stat-value" id="calStatWinRate">—</div>
        <div class="cal-stat-label">Win Rate</div>
      </div>
      <div class="cal-stat-box">
        <div class="cal-stat-value" id="calStatGreen">0</div>
        <div class="cal-stat-label">Green Days</div>
      </div>
      <div class="cal-stat-box">
        <div class="cal-stat-value" id="calStatRed">0</div>
        <div class="cal-stat-label">Red Days</div>
      </div>
    </div>
    <div class="pnl-calendar-header">
      <div class="pnl-calendar-nav">
        <button onclick="calPrev()">&#9664;</button>
        <span class="cal-month-label" id="calMonthLabel">—</span>
        <button onclick="calNext()">&#9654;</button>
      </div>
    </div>
    <div class="pnl-calendar-grid" id="calGrid">
      <div class="pnl-cal-dayheader">Sun</div>
      <div class="pnl-cal-dayheader">Mon</div>
      <div class="pnl-cal-dayheader">Tue</div>
      <div class="pnl-cal-dayheader">Wed</div>
      <div class="pnl-cal-dayheader">Thu</div>
      <div class="pnl-cal-dayheader">Fri</div>
      <div class="pnl-cal-dayheader">Sat</div>
    </div>
    <div class="pnl-cal-summary" id="calSummary" style="display:none;">
      <div class="pnl-cal-summary-item">
        <span class="pnl-cal-summary-label">Best Day</span>
        <span class="pnl-cal-summary-value" id="calSumBest">—</span>
      </div>
      <div class="pnl-cal-summary-item">
        <span class="pnl-cal-summary-label">Worst Day</span>
        <span class="pnl-cal-summary-value" id="calSumWorst">—</span>
      </div>
      <div class="pnl-cal-summary-item">
        <span class="pnl-cal-summary-label">Avg Day</span>
        <span class="pnl-cal-summary-value" id="calSumAvg">—</span>
      </div>
      <div class="pnl-cal-summary-item">
        <span class="pnl-cal-summary-label">Days Tracked</span>
        <span class="pnl-cal-summary-value" id="calSumDays">—</span>
      </div>
    </div>
  </div>

  <div class="footer">
    Strategy Factory v3.0 — Generated {ts}<br>
    <span style="font-size:0.85em;">For informational and educational purposes only. Not financial advice.</span>
  </div>
</div>"""

    # ── PAGE: PORTFOLIO ──────────────────────────────────────────────────
    def _page_portfolio(self, evaluations, portfolio):
        return """<div class="page" id="portfolio">
  <div class="page-title"><span class="accent">💼</span> Portfolio</div>

  <!-- LIVE ACCOUNT HERO (populated via JS from Alpaca) -->
  <div class="portfolio-hero">
    <div class="portfolio-hero-subtitle">Alpaca Account Equity</div>
    <div class="portfolio-hero-value" id="pfEquity">$—</div>
    <div class="portfolio-hero-subtitle" id="pfPLsub">Loading...</div>
  </div>

  <!-- Account cards -->
  <div class="cards-row" style="margin-bottom:24px;">
    <div class="card"><div class="card-label">Buying Power</div><div id="pfCash" class="card-value">$—</div><div class="card-sub">Available to trade</div></div>
    <div class="card"><div class="card-label">Unrealized P&L</div><div id="pfPL" class="card-value">$—</div><div class="card-sub">Open positions vs cost</div></div>
    <div class="card"><div class="card-label">Open Positions</div><div id="pfPosCount" class="card-value">—</div><div class="card-sub">Currently held</div></div>
    <div class="card"><div class="card-label">Today's P&L</div><div id="pfDayPL" class="card-value">$—</div><div class="card-sub">Since last close</div></div>
  </div>

  <!-- Live Positions Table (populated via JS) -->
  <h3 style="margin-bottom:16px;">Current Holdings</h3>
  <div id="pfPositionsTable" style="margin-bottom:28px;">
    <div style="padding:24px;text-align:center;color:var(--text-dim);background:var(--card);border:1px solid var(--border);border-radius:12px;">Loading positions from Alpaca...</div>
  </div>

  <!-- Bot Activity Timeline (Gantt) -->
  <div class="chart-box" style="margin-bottom:28px;">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:14px;">
      <h3>📊 Trade Timeline</h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <label style="font-size:0.8em;color:var(--text-dim);">From:</label>
        <input type="date" id="ganttFrom" style="background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:0.85em;" />
        <label style="font-size:0.8em;color:var(--text-dim);">To:</label>
        <input type="date" id="ganttTo" style="background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:0.85em;" />
        <button class="filter-btn" onclick="ganttApplyDates()" style="padding:6px 14px;">Apply</button>
      </div>
    </div>
    <div id="ganttContainer" style="overflow-x:auto;min-height:200px;">
      <div style="padding:30px;text-align:center;color:var(--text-dim);font-size:0.9em;">Loading trade timeline...</div>
    </div>
  </div>

  <div class="disclaimer">⚠️ Alpaca paper trading account — no real money. Past performance does not guarantee future results.</div>
</div>"""

    # ── PAGE: PAPER TRADING ──────────────────────────────────────────────
    def _page_alpaca(self):
        return """<div class="page" id="alpaca">
  <div class="page-title"><span class="accent">🔗</span> Alpaca Trading</div>

  <!-- Connection Status -->
  <div id="alpacaConnCard" class="card" style="margin-bottom:24px;">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;">
      <div>
        <div class="card-label">Alpaca Connection</div>
        <div id="alpacaConnStatus" class="card-value" style="font-size:1.3em;color:var(--gray);">⚪ Not Connected</div>
        <div id="alpacaConnMsg" class="card-sub">Connect to your Alpaca paper trading account</div>
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <button class="filter-btn" style="padding:12px 22px;font-weight:600;" onclick="alpacaConnect()">🔌 Connect</button>
      </div>
    </div>
  </div>

  <!-- Account Info (hidden until connected) -->
  <div id="alpacaAccountSection" style="display:none;">
    <div class="cards-row">
      <div class="card">
        <div class="card-label">Equity</div>
        <div id="alpEquity" class="card-value">$—</div>
        <div class="card-sub">Cash + market value</div>
      </div>
      <div class="card">
        <div class="card-label">Cash</div>
        <div id="alpCash" class="card-value">$—</div>
        <div class="card-sub">Available to trade</div>
      </div>
      <div class="card">
        <div class="card-label">Buying Power</div>
        <div id="alpBP" class="card-value">$—</div>
        <div class="card-sub">Same as cash (no margin)</div>
      </div>
      <div class="card">
        <div class="card-label">Unrealized P&L</div>
        <div id="alpPL" class="card-value">$—</div>
        <div class="card-sub">Open positions vs cost</div>
      </div>
      <div class="card">
        <div class="card-label">Positions</div>
        <div id="alpPosCount" class="card-value">—</div>
        <div class="card-sub">Currently open</div>
      </div>
      <div class="card">
        <div class="card-label">Account #</div>
        <div id="alpAcctNum" class="card-value" style="font-size:0.95em;">—</div>
        <div class="card-sub">Local simulator</div>
      </div>
    </div>

    <!-- Action Buttons -->
    <div style="background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;margin-bottom:24px;">
      <h3 style="margin-bottom:16px;">Portfolio Actions</h3>
      <div style="display:flex;gap:12px;flex-wrap:wrap;">
        <button class="filter-btn" style="padding:12px 22px;font-weight:600;" onclick="alpacaRefreshDailyRun()">🔄 Refresh Analysis</button>
        <button class="filter-btn" style="padding:12px 22px;font-weight:600;" onclick="alpacaPreview()">👁️ Preview Orders</button>
        <button class="filter-btn" style="padding:12px 22px;font-weight:600;background:rgba(57,255,20,0.15);color:var(--lime);border-color:var(--lime);" onclick="alpacaConfirmExecute()">▶️ Execute Paper Orders</button>
        <button class="filter-btn" style="padding:12px 22px;font-weight:600;background:rgba(255,68,68,0.1);color:var(--red);border-color:var(--red);" onclick="alpacaConfirmCloseAll()">⛔ Close All Positions</button>
      </div>
      <div id="alpActionMsg" style="margin-top:14px;padding:12px;background:rgba(0,212,255,0.05);border-radius:8px;font-size:0.88em;color:var(--text-dim);display:none;"></div>
    </div>

    <!-- Auto-Trading Control -->
    <div style="background:linear-gradient(135deg,rgba(57,255,20,0.05) 0%,rgba(0,212,255,0.05) 100%);border:1px solid var(--border);border-radius:14px;padding:24px;margin-bottom:24px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:14px;">
        <div style="flex:1;min-width:280px;">
          <h3 style="margin-bottom:8px;display:flex;align-items:center;gap:10px;">🤖 Auto-Trading <span id="autoStateBadge" class="badge" style="background:rgba(107,115,148,0.15);color:var(--gray);">OFF</span></h3>
          <div style="color:var(--text-dim);font-size:0.9em;line-height:1.5;" id="autoDescription">
            When enabled, the system re-analyzes all bots every 15 minutes and automatically rebalances your paper portfolio. No clicks needed — just check the dashboard each morning.
          </div>
        </div>
        <div style="display:flex;gap:10px;flex-direction:column;align-items:flex-end;">
          <button id="autoToggleBtn" class="filter-btn" style="padding:14px 28px;font-weight:700;font-size:1em;" onclick="autoToggle()">▶️ Enable Auto-Trading</button>
          <button class="filter-btn" style="padding:8px 18px;font-size:0.85em;" onclick="autoRunNow()">⚡ Run Cycle Now</button>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:20px;padding-top:16px;border-top:1px solid var(--border);">
        <div>
          <div style="font-size:0.72em;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;">Interval</div>
          <div id="autoInterval" style="font-family:'Courier New',monospace;color:var(--cyan);font-weight:600;">15 min</div>
        </div>
        <div>
          <div style="font-size:0.72em;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;">Last Run</div>
          <div id="autoLastRun" style="font-family:'Courier New',monospace;color:var(--cyan);font-weight:600;">—</div>
        </div>
        <div>
          <div style="font-size:0.72em;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;">Next Run</div>
          <div id="autoNextRun" style="font-family:'Courier New',monospace;color:var(--cyan);font-weight:600;">—</div>
        </div>
        <div>
          <div style="font-size:0.72em;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;">Recent Result</div>
          <div id="autoLastResult" style="font-family:'Courier New',monospace;font-weight:600;">—</div>
        </div>
      </div>
      <div id="autoRunsLog" style="margin-top:14px;"></div>
    </div>

    <!-- Order Preview -->
    <div id="alpPreviewSection" style="display:none;margin-bottom:24px;">
      <h3 style="margin-bottom:16px;">Order Preview</h3>
      <div id="alpPreviewSummary" style="margin-bottom:12px;color:var(--text-dim);"></div>
      <div class="table-wrap"><table class="data-table compact" id="alpPreviewTable">
        <thead><tr>
          <th>Bot</th><th>Symbol</th><th>Side</th><th>Notional</th>
          <th>Target</th><th>Current</th><th>Status</th>
        </tr></thead>
        <tbody id="alpPreviewBody"></tbody>
      </table></div>
      <div id="alpPreviewSkipped" style="margin-top:12px;"></div>
    </div>

    <!-- Positions -->
    <div style="margin-bottom:24px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <h3>Current Positions</h3>
        <button class="filter-btn" onclick="alpacaLoadPositions()">🔄 Refresh</button>
      </div>
      <div id="alpPositionsBody">
        <div style="padding:24px;text-align:center;color:var(--text-dim);background:var(--card);border:1px solid var(--border);border-radius:12px;">
          No positions yet. Click "Preview Orders" then "Execute Paper Orders" to open positions.
        </div>
      </div>
    </div>

    <!-- Recent Orders -->
    <div style="margin-bottom:24px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <h3>Recent Orders</h3>
        <div style="display:flex;gap:8px;align-items:center;">
          <button class="filter-btn active" id="orderFilterAll" onclick="filterOrders('all')">All</button>
          <button class="filter-btn" id="orderFilterBuy" onclick="filterOrders('buy')">Buy</button>
          <button class="filter-btn" id="orderFilterSell" onclick="filterOrders('sell')">Sell</button>
          <button class="filter-btn" onclick="alpacaLoadOrders()">🔄 Refresh</button>
        </div>
      </div>
      <div id="alpOrdersBody">
        <div style="padding:24px;text-align:center;color:var(--text-dim);background:var(--card);border:1px solid var(--border);border-radius:12px;">
          No orders yet.
        </div>
      </div>
    </div>
  </div>

  <div class="disclaimer">
    ⚠️ Alpaca paper trading — no real money is involved. Orders are executed via the Alpaca API. Past performance does not guarantee future results.
  </div>
</div>

<!-- Confirmation Modal -->
<div id="alpModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(10,14,39,0.85);z-index:10000;align-items:center;justify-content:center;">
  <div style="background:var(--card);border:1px solid var(--cyan);border-radius:14px;padding:32px;max-width:500px;width:90%;box-shadow:0 20px 60px rgba(0,0,0,0.6);">
    <h3 id="alpModalTitle" style="color:var(--cyan);margin-bottom:16px;font-size:1.3em;">Confirm Action</h3>
    <p id="alpModalBody" style="color:var(--text-dim);line-height:1.6;margin-bottom:24px;"></p>
    <div style="display:flex;gap:12px;justify-content:flex-end;">
      <button class="filter-btn" style="padding:10px 20px;" onclick="alpModalCancel()">Cancel</button>
      <button id="alpModalConfirmBtn" class="filter-btn" style="padding:10px 20px;background:rgba(57,255,20,0.15);color:var(--lime);border-color:var(--lime);font-weight:600;">Confirm</button>
    </div>
  </div>
</div>"""

    # ── PAGE: ALPACA LIVE ────────────────────────────────────────────────
    def _page_alpaca_live(self):
        return """<div class="page" id="alpaca-live">
  <div class="page-title"><span class="accent">🔗</span> Alpaca Trading</div>
  <p class="page-sub" style="color:var(--text-dim);margin-bottom:24px;">Live connection to your Alpaca paper trading account</p>

  <!-- Broker Selector -->
  <div class="section-card">
    <div class="section-header" style="margin-bottom:0;">
      <div>
        <div class="section-title">Trading Mode</div>
        <div class="section-sub">Read-only command center for automatic Alpaca paper trading. Manual order entry and manual closes are intentionally hidden.</div>
      </div>
      <div class="status-pill ok"><span class="live-dot" style="margin-right:0;"></span> Alpaca Paper · Automatic</div>
    </div>
  </div>

  <!-- Connection Card -->
  <div id="alpLiveConnCard" class="section-card">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
      <div>
        <div style="font-size:0.9em;color:var(--text-dim);margin-bottom:4px;">Connection Status</div>
        <div id="alpLiveConnStatus" class="card-value" style="font-size:1.3em;color:var(--gray);">⚪ Not Connected</div>
        <div id="alpLiveConnMsg" class="card-sub">Auto-connects when Alpaca keys are configured</div>
      </div>
      <div id="alpLiveConnBtn" class="status-pill warn">Auto-connecting</div>
    </div>
  </div>

  <!-- Account Summary (hidden until connected) -->
  <div id="alpLiveAccountSection" style="display:none;">
    <div class="metric-grid">
      <div class="metric-card"><div class="metric-label">Equity</div><div id="alpLiveEquity" class="metric-value">—</div><div class="metric-sub">Cash plus live positions</div></div>
      <div class="metric-card"><div class="metric-label">Cash / Buying Power</div><div id="alpLiveCash" class="metric-value">—</div><div class="metric-sub">Available to deploy</div></div>
      <div class="metric-card"><div class="metric-label">Today's P&L</div><div id="alpLivePL" class="metric-value">—</div><div class="metric-sub">Live equity vs last close</div></div>
      <div class="metric-card"><div class="metric-label">Account</div><div id="alpLiveAccNum" class="metric-value" style="font-size:1.05em;">—</div><div class="metric-sub">Paper trading</div></div>
    </div>

    <!-- Positions -->
    <div class="section-card">
      <div class="section-header">
        <div>
          <div class="section-title">Open Positions</div>
          <div class="section-sub">Live marks, risk prices, strategy context, and entry reason. Updates every second from the same quote snapshot as Overview and Portfolio.</div>
        </div>
        <div class="status-pill ok"><span class="live-dot" style="margin-right:0;"></span> 1s live sync</div>
      </div>
      <div id="alpLivePositionsEmpty" style="color:var(--text-dim);padding:20px 0;text-align:center;">No open positions</div>
      <div id="alpLivePositionsTable" class="table-wrap" style="display:none;">
        <table class="data-table compact trade-monitor-table">
          <thead><tr>
            <th>Symbol</th><th class="num">Live Price</th><th class="num">Value</th><th class="num">P&L</th>
            <th>Risk Prices</th><th>Strategy</th><th>Regime</th>
          </tr></thead>
          <tbody id="alpLivePositionsBody"></tbody>
        </table>
      </div>
      <div id="alpLivePosSummary" style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);display:none;">
        <span style="color:var(--text-dim);font-size:0.9em;">Total: </span>
        <span id="alpLivePosTotal" style="font-weight:600;">—</span>
      </div>
    </div>

    <!-- Fee-aware P&L -->
    <div class="section-card">
      <div class="section-header">
        <div>
          <div class="section-title">Fee & Net P&L</div>
          <div class="section-sub">Estimated Alpaca crypto fees. Paper trades do not pay real fees, but this shows live-style net results.</div>
          <div id="alpFeeLedgerPath" class="section-sub" style="font-size:0.78em;margin-top:4px;">CSV ledger: loading...</div>
        </div>
      </div>
      <div class="metric-grid">
        <div class="metric-card"><div class="metric-label">Realized Net P&L</div><div id="alpFeeRealizedNet" class="metric-value">—</div></div>
        <div class="metric-card"><div class="metric-label">Estimated Fees Paid</div><div id="alpFeeRealizedFees" class="metric-value">—</div></div>
        <div class="metric-card"><div class="metric-label">Net Win Rate</div><div id="alpFeeWinRate" class="metric-value">—</div></div>
        <div class="metric-card"><div class="metric-label">Open Net If Closed</div><div id="alpFeeOpenNet" class="metric-value">—</div></div>
      </div>
      <div style="font-weight:600;margin:10px 0 8px;">Open Fee Preview</div>
      <div id="alpFeeOpenEmpty" style="color:var(--text-dim);padding:12px 0;">No open positions to estimate.</div>
      <div id="alpFeeOpenTable" class="table-wrap" style="display:none;margin-bottom:16px;">
        <table class="data-table compact">
          <thead><tr>
            <th>Symbol</th><th class="num">Bought</th><th class="num">Mark / Sell Now</th><th class="num">Gross P&L</th><th class="num">Est. Fees</th><th class="num">Net In Hand</th>
          </tr></thead>
          <tbody id="alpFeeOpenBody"></tbody>
        </table>
      </div>
      <div style="font-weight:600;margin:10px 0 8px;">Closed Trades</div>
      <div id="alpFeeClosedEmpty" style="color:var(--text-dim);padding:12px 0;">No closed bot trades with fee analysis yet.</div>
      <div id="alpFeeClosedTable" class="table-wrap" style="display:none;">
        <table class="data-table compact">
          <thead><tr>
            <th>Time</th><th>Symbol</th><th class="num">Buy</th><th class="num">Sell</th><th class="num">Gross P&L</th><th class="num">Fees</th><th class="num">Net In Hand</th><th>Why Sold</th>
          </tr></thead>
          <tbody id="alpFeeClosedBody"></tbody>
        </table>
      </div>
    </div>

    <!-- Recent Orders -->
    <div class="section-card">
      <div class="section-header">
        <div>
          <div class="section-title">Recent Orders</div>
          <div class="section-sub">Broker fills and order status. This is audit context only; no manual order entry is exposed.</div>
        </div>
      </div>
      <div id="alpLiveOrdersEmpty" style="color:var(--text-dim);padding:20px 0;text-align:center;">No recent orders</div>
      <div id="alpLiveOrdersTable" class="table-wrap" style="display:none;">
        <table class="data-table compact">
          <thead><tr>
            <th>Time</th><th>Symbol</th><th>Side</th><th class="num">Amount</th>
            <th class="num">Fill Price</th><th>Status</th>
          </tr></thead>
          <tbody id="alpLiveOrdersBody"></tbody>
        </table>
      </div>
    </div>

    <!-- Real Paper Journal -->
    <div class="section-card">
      <div class="section-header">
        <div>
          <div class="section-title">Trade & Decision Journal</div>
          <div class="section-sub">Real Alpaca paper decisions only. Seeded/backtest metrics are not included here.</div>
        </div>
      </div>
    <div id="alpLiveJournalBody" style="max-height:280px;overflow:auto;color:var(--text-dim);font-size:0.88em;">No journal events loaded.</div>
    </div>

    <div class="section-card">
      <div class="section-header">
        <div>
          <div class="section-title">Live Guards & Exit Readout</div>
          <div class="section-sub">Shows when the system is being throttled, what is being blocked, and which exit reasons are actually firing.</div>
        </div>
      </div>
      <div class="metric-grid">
        <div class="metric-card"><div class="metric-label">Cooldown Symbols</div><div id="alpGuardCooldown" class="metric-value">—</div><div class="metric-sub">Loss exits in cooldown window</div></div>
        <div class="metric-card"><div class="metric-label">Daily Trade Caps</div><div id="alpGuardTradeCaps" class="metric-value">—</div><div class="metric-sub">Symbols at max entries today</div></div>
        <div class="metric-card"><div class="metric-label">Loss Exits 24h</div><div id="alpGuardLossExits" class="metric-value">—</div><div class="metric-sub">Recent red closes</div></div>
        <div class="metric-card"><div class="metric-label">Learning Blocks</div><div id="alpGuardBlocks" class="metric-value">—</div><div class="metric-sub">Blocked strategy-regime pairs</div></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
          <div style="font-weight:600;margin:10px 0 8px;">Recent Exit Reasons</div>
          <div id="alpGuardExitEmpty" style="color:var(--text-dim);padding:12px 0;">No recent exit reasons yet.</div>
          <div id="alpGuardExitTable" class="table-wrap" style="display:none;">
            <table class="data-table compact">
              <thead><tr><th>Exit Type</th><th class="num">Count</th></tr></thead>
              <tbody id="alpGuardExitBody"></tbody>
            </table>
          </div>
        </div>
        <div>
          <div style="font-weight:600;margin:10px 0 8px;">Current Learning Blocks</div>
          <div id="alpGuardBlockEmpty" style="color:var(--text-dim);padding:12px 0;">No strategy blocks active.</div>
          <div id="alpGuardBlockTable" class="table-wrap" style="display:none;">
            <table class="data-table compact">
              <thead><tr><th>Strategy</th><th>Regime</th><th class="num">Trades</th><th class="num">Win Rate</th></tr></thead>
              <tbody id="alpGuardBlockBody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- Auto-Trade Controls -->
    <div class="section-card">
      <div class="section-header">
        <div>
          <div class="section-title">Auto-Trade (Alpaca)</div>
          <div class="section-sub">Automatic cycle status, cadence, and recent runs. Trading controls stay server-side so the dashboard is a monitoring surface, not a manual order pad.</div>
        </div>
        <span id="alpAutoStatusBadge" class="status-pill">OFF</span>
      </div>

      <!-- Progress + results area -->
      <div id="alpAutoProgressArea" style="display:none;margin-bottom:16px;padding:16px;background:rgba(0,212,255,0.05);border:1px solid var(--border);border-radius:10px;">
        <div id="alpAutoProgressSteps" style="font-size:0.9em;"></div>
        <div id="alpAutoProgressResult" style="margin-top:10px;font-size:0.9em;color:var(--text-dim);"></div>
      </div>

      <!-- Status info -->
      <div class="metric-grid" style="margin-bottom:16px;">
        <div class="metric-card">
          <div class="metric-label">Status</div>
          <div id="alpAutoRunStatus" class="metric-value" style="font-size:1.1em;">—</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Last Run</div>
          <div id="alpAutoLastRun" class="metric-value" style="font-size:1.1em;">Never</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Next Run</div>
          <div id="alpAutoNextRun" class="metric-value" style="font-size:1.1em;">—</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Interval</div>
          <div id="alpAutoInterval" class="metric-value" style="font-size:1.1em;">15 min</div>
        </div>
      </div>

      <!-- Recent auto-runs log -->
      <div style="font-weight:600;font-size:0.95em;margin-bottom:8px;">Recent Auto-Runs</div>
      <div id="alpAutoLogsEmpty" style="color:var(--text-dim);font-size:0.85em;">No auto-runs recorded yet. Automatic cycles will appear here after the scheduler runs.</div>
      <div id="alpAutoLogs" style="display:none;max-height:250px;overflow-y:auto;font-size:0.85em;"></div>
    </div>
  </div>

  <!-- Not-configured message (shown when keys are missing) -->
  <div id="alpLiveNotConfigured" style="display:none;">
    <div class="card" style="text-align:center;padding:40px;">
      <div style="font-size:2em;margin-bottom:16px;">🔑</div>
      <div style="font-size:1.1em;color:var(--text);margin-bottom:8px;">Alpaca API Keys Not Configured</div>
      <div style="color:var(--text-dim);line-height:1.6;max-width:500px;margin:0 auto;">
        Add these environment variables to Railway:<br>
        <code style="background:var(--bg);padding:2px 8px;border-radius:4px;color:var(--cyan);">ALPACA_API_KEY</code> and
        <code style="background:var(--bg);padding:2px 8px;border-radius:4px;color:var(--cyan);">ALPACA_API_SECRET</code><br>
        <span style="font-size:0.85em;margin-top:8px;display:inline-block;">Get your free paper trading keys at <a href="https://app.alpaca.markets" target="_blank" style="color:var(--cyan);">app.alpaca.markets</a></span>
      </div>
    </div>
  </div>
</div>"""

    # ── PAGE: QUANTUM MATRIX ─────────────────────────────────────────────
    def _page_quantum(self, evaluations):
        rows = ""
        for bot_name, ed in evaluations.items():
            v = ed.get("verdict", "HOLD").upper()
            wr = self._num(ed.get("win_rate", 0))
            pf = self._num(ed.get("profit_factor", 0))
            md = self._num(ed.get("max_drawdown", 0))
            adapt = self._num(ed.get("adaptation_score", 0))
            if v == "REACTIVATE":
                action = "Candidate"
                action_sub = "May trade if real paper data agrees"
            elif v == "PAUSE":
                action = "Pause"
                action_sub = "Do not trust until evidence improves"
            else:
                action = "Monitor"
                action_sub = "No strong action from seed data"
            rows += f"""<tr class="row-{v.lower()}" data-verdict="{v}">
      <td><strong>{bot_name}</strong><span class="muted-line">Seed/backtest only until real paper exits exist</span></td>
      <td><span class="badge badge-{v.lower()}">{v}</span><span class="muted-line">{action_sub}</span></td>
      <td class="num">{adapt:.0f}/100{self._quality_badge('adapt', adapt)}</td>
      <td class="num">{wr:.1f}%{self._quality_badge('wr', wr)}</td>
      <td class="num">{pf:.2f}{self._quality_badge('pf', pf)}</td>
      <td class="num">{md:.1f}%{self._quality_badge('dd', md)}</td>
      <td><strong>{action}</strong></td>
    </tr>"""

        return f"""<div class="page" id="quantum">
  <div class="page-title"><span class="accent">⚛️</span> Strategy Scorecard <span class="data-badge">SEED DATA + REAL PAPER OVERLAY</span></div>
  <p class="page-sub">This page is for deciding which strategy families deserve trust. Seed metrics are only a starting rank; the live table below is updated from the Alpaca paper ledger when closed trades exist.</p>
  <div class="metric-grid">
    <div class="metric-card"><div class="metric-label">Real Paper Strategies</div><div class="metric-value" id="scoreRealStrategies">—</div><div class="metric-sub">Strategies with closed paper trades</div></div>
    <div class="metric-card"><div class="metric-label">Best Real Strategy</div><div class="metric-value" id="scoreBestStrategy" style="font-size:1.35em;">—</div><div class="metric-sub" id="scoreBestStrategySub">Waiting for closed trades</div></div>
    <div class="metric-card"><div class="metric-label">Needs Review</div><div class="metric-value" id="scoreNeedsReview" style="color:var(--amber);">—</div><div class="metric-sub">Weak or thin real evidence</div></div>
  </div>
  <div class="section-card">
    <div class="section-header">
      <div><div class="section-title">Real Paper Strategy Board</div><div class="section-sub">Trust this table over seed scores once enough Alpaca paper exits have accumulated.</div></div>
      <span class="status-pill" id="scoreLiveStatus">Loading</span>
    </div>
    <div id="scoreRealEmpty" class="read-only-note">No closed Alpaca paper trades yet. Seed scorecard remains visible below, but it is not proof of live edge.</div>
    <div id="scoreRealTable" class="table-wrap" style="display:none;"><table class="data-table compact">
      <thead><tr><th>Strategy</th><th class="num">Closed</th><th class="num">Net P&L</th><th class="num">Win Rate</th><th class="num">Avg Net</th><th>Action</th><th>Evidence</th></tr></thead>
      <tbody id="scoreRealBody"></tbody>
    </table></div>
  </div>
  <div class="filter-buttons">
    <button class="filter-btn active" onclick="filterQuantum('ALL')">Show All</button>
    <button class="filter-btn" onclick="filterQuantum('PAUSE')">Pause</button>
    <button class="filter-btn" onclick="filterQuantum('HOLD')">Hold</button>
    <button class="filter-btn" onclick="filterQuantum('REACTIVATE')">Reactivate</button>
  </div>
  <div class="table-wrap"><table class="data-table compact" id="quantumTable">
    <thead><tr>
      <th onclick="sortTable(0)">Strategy</th>
      <th onclick="sortTable(1)">Verdict</th>
      <th onclick="sortTable(2)" class="num">Seed Fit</th>
      <th onclick="sortTable(3)" class="num">Seed Win Rate</th>
      <th onclick="sortTable(4)" class="num">Seed Profit Factor</th>
      <th onclick="sortTable(5)" class="num">Seed Worst Drop</th>
      <th onclick="sortTable(6)">Operator Action</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table></div>
</div>"""

    # ── PAGE: BOT STATUS ─────────────────────────────────────────────────
    def _page_bots(self, bots_data, evaluations):
        rows = ""
        for bot in bots_data:
            name = bot.get("name", "Unknown")
            pair = bot.get("pair", "N/A")
            status = bot.get("status", "unknown")
            ed = self._evaluation_for_bot(bot, evaluations)
            status = ed.get("bot_status", status)
            verdict = ed.get("verdict", "HOLD")
            adapt = self._num(ed.get("adaptation_score", 50))
            rows += f"""<tr>
      <td><strong>{name}</strong><span class="muted-line">{pair}</span></td>
      <td><span class="badge badge-{status}">{status}</span></td>
      <td><span class="badge badge-{verdict.lower()}">{verdict}</span></td>
      <td class="num">{adapt:.0f}/100</td>
      <td>Seed bot available. Live signal state appears above after the intraday engine runs.</td>
    </tr>"""

        return f"""<div class="page" id="bots">
  <div class="page-title"><span class="accent">🤖</span> Bot Signals <span class="data-badge">LIVE INTRADAY STATE</span></div>
  <p class="page-sub">Use this page to understand what the bot is seeing right now: signal, confidence, regime, and why a symbol was accepted or rejected.</p>
  <div class="metric-grid">
    <div class="metric-card"><div class="metric-label">Symbols Checked</div><div class="metric-value" id="signalsSymbols">—</div><div class="metric-sub">Latest intraday cycle</div></div>
    <div class="metric-card"><div class="metric-label">Tradable Signals</div><div class="metric-value" id="signalsTradable" style="color:var(--lime);">—</div><div class="metric-sub">Passed quality gate</div></div>
    <div class="metric-card"><div class="metric-label">Rejected / Waiting</div><div class="metric-value" id="signalsRejected" style="color:var(--amber);">—</div><div class="metric-sub">Skipped with reason</div></div>
    <div class="metric-card"><div class="metric-label">Learning Blocks</div><div class="metric-value" id="signalsBlocked" style="color:var(--red);">—</div><div class="metric-sub">Strategy-regime pairs suppressed</div></div>
    <div class="metric-card"><div class="metric-label">Top Reject Driver</div><div class="metric-value" id="signalsTopReject" style="font-size:1.1em;">—</div><div class="metric-sub">Most common skip reason</div></div>
  </div>
  <div class="section-card">
    <div class="section-header">
      <div><div class="section-title">Current Signal Board</div><div class="section-sub">Real intraday engine output. This is the clearest place to see why the system may buy, hold, or skip.</div></div>
      <span class="status-pill" id="signalsLiveStatus">Loading</span>
    </div>
    <div id="signalsEmpty" class="read-only-note">No intraday signal state has been saved yet.</div>
    <div id="signalsTable" class="table-wrap" style="display:none;"><table class="data-table compact">
      <thead><tr><th>Symbol</th><th>Decision</th><th class="num">Confidence</th><th>Strategy</th><th>Regime</th><th>Reason / Blocker</th></tr></thead>
      <tbody id="signalsBody"></tbody>
    </table></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Configured Bot Inventory</div><div class="section-sub">Seed/backtest bot list. This is configuration context, not proof of live trading skill.</div></div></div>
    <div class="table-wrap"><table class="data-table compact"><thead><tr><th>Bot</th><th>Status</th><th>Seed Verdict</th><th class="num">Seed Fit</th><th>Note</th></tr></thead><tbody>{rows}</tbody></table></div>
  </div>
</div>"""

    # ── PAGE: PERFORMANCE ────────────────────────────────────────────────
    def _page_performance(self, evaluations):
        return """<div class="page" id="performance">
  <div class="page-title"><span class="accent">📈</span> Performance <span class="data-badge">REAL PAPER LEDGER</span></div>
  <p class="page-sub">This page ignores seeded performance and focuses on closed Alpaca paper trades after estimated fees. It is meant to answer what actually worked, what lost, and whether fees are eating the edge.</p>
  <div class="metric-grid">
    <div class="metric-card"><div class="metric-label">Closed Trades</div><div class="metric-value" id="perfTrades">—</div><div class="metric-sub">Fee-aware ledger rows</div></div>
    <div class="metric-card"><div class="metric-label">Net P&L After Fees</div><div class="metric-value" id="perfNet">—</div><div class="metric-sub">Estimated Alpaca crypto fees included</div></div>
    <div class="metric-card"><div class="metric-label">Win Rate</div><div class="metric-value" id="perfWinRate">—</div><div class="metric-sub">Net winners only</div></div>
    <div class="metric-card"><div class="metric-label">Fees Paid</div><div class="metric-value" id="perfFees" style="color:var(--amber);">—</div><div class="metric-sub">Estimated round-trip cost</div></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Strategy Results</div><div class="section-sub">Review which strategy families deserve more capital or less trust.</div></div></div>
    <div id="perfStrategyEmpty" class="read-only-note">Waiting for closed trades.</div>
    <div id="perfStrategyTable" class="table-wrap" style="display:none;"><table class="data-table compact">
      <thead><tr><th>Strategy</th><th class="num">Trades</th><th class="num">Net P&L</th><th class="num">Win Rate</th><th class="num">Avg Net</th><th class="num">Fees</th><th>Read</th></tr></thead>
      <tbody id="perfStrategyBody"></tbody>
    </table></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Symbol Results</div><div class="section-sub">Find symbols that are helping or hurting the system.</div></div></div>
    <div id="perfSymbolEmpty" class="read-only-note">Waiting for closed trades.</div>
    <div id="perfSymbolTable" class="table-wrap" style="display:none;"><table class="data-table compact">
      <thead><tr><th>Symbol</th><th class="num">Trades</th><th class="num">Net P&L</th><th class="num">Win Rate</th><th class="num">Avg Net</th><th class="num">Fees</th></tr></thead>
      <tbody id="perfSymbolBody"></tbody>
    </table></div>
  </div>
</div>"""

    # ── PAGE: LEARNING ENGINE ────────────────────────────────────────────
    def _page_learning(self, learning_stats, evaluations):
        rows = ""
        for bot_name, stats in learning_stats.items():
            if bot_name.startswith("_"):
                continue
            adapt = self._num(stats.get("adaptation_score", 0))
            real_score = stats.get("real_paper_score")
            real_closed = int(stats.get("real_paper_closed_trades", 0) or 0)
            real_wr = stats.get("real_paper_win_rate")
            real_avg = stats.get("real_paper_avg_pl_pct")
            trust = "Seed only"
            if real_closed >= 10:
                trust = "Trust real score"
            elif real_closed >= 3:
                trust = "Building evidence"
            wr_text = f"{real_wr:.1f}%" if real_wr is not None else "—"
            avg_text = f"{real_avg:+.2f}%" if real_avg is not None else "—"
            score_text = f"{real_score:.0f}" if real_score is not None else "Collecting"
            rows += f"""<tr>
      <td><strong>{bot_name}</strong><span class="muted-line">{trust}</span></td>
      <td class="num">{score_text}</td>
      <td class="num">{real_closed}</td>
      <td class="num">{wr_text}</td>
      <td class="num">{avg_text}</td>
      <td class="num">{adapt:.0f}/100</td>
    </tr>"""

        return f"""<div class="page" id="learning">
  <div class="page-title"><span class="accent">🧠</span> Learning Engine <span class="data-badge">REAL PAPER LEARNING</span></div>
  <p class="page-sub">This is not magic AI. It is evidence tracking: closed trades, net outcomes, sample size, and whether a strategy should be boosted, monitored, or downweighted.</p>
  <div class="metric-grid">
    <div class="metric-card"><div class="metric-label">Trusted Strategies</div><div class="metric-value" id="learnTrusted">—</div><div class="metric-sub">10+ closed trades</div></div>
    <div class="metric-card"><div class="metric-label">Collecting Data</div><div class="metric-value" id="learnCollecting">—</div><div class="metric-sub">Not enough exits yet</div></div>
    <div class="metric-card"><div class="metric-label">Downweight Candidates</div><div class="metric-value" id="learnDownweight" style="color:var(--red);">—</div><div class="metric-sub">Negative or weak real evidence</div></div>
    <div class="metric-card"><div class="metric-label">Active Blocks</div><div class="metric-value" id="learnBlocked" style="color:var(--red);">—</div><div class="metric-sub">Strategy-regime pairs currently blocked</div></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Real Paper Learning Board</div><div class="section-sub">Operator readout from the persistent fee-aware ledger.</div></div><span class="status-pill" id="learnLiveStatus">Loading</span></div>
    <div id="learnRealEmpty" class="read-only-note">No closed Alpaca paper trades yet. Real learning starts after exits are recorded.</div>
    <div id="learnRealTable" class="table-wrap" style="display:none;"><table class="data-table compact">
      <thead><tr><th>Strategy</th><th class="num">Closed</th><th class="num">Net P&L</th><th class="num">Win Rate</th><th class="num">Avg Net</th><th>Learning Action</th><th>Reason</th></tr></thead>
      <tbody id="learnRealBody"></tbody>
    </table></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Real Regime Blocks</div><div class="section-sub">These are the strategy/regime combinations the live learner is actively suppressing based only on real paper outcomes.</div></div></div>
    <div id="learnBlockEmpty" class="read-only-note">No active real-performance blocks right now.</div>
    <div id="learnBlockTable" class="table-wrap" style="display:none;"><table class="data-table compact">
      <thead><tr><th>Strategy</th><th>Regime</th><th class="num">Trades</th><th class="num">Win Rate</th><th class="num">Net P&L</th><th>Reason</th></tr></thead>
      <tbody id="learnBlockBody"></tbody>
    </table></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Seed Baseline</div><div class="section-sub">Initial ranking only. Never treat this as real trading performance.</div></div></div>
    <div class="table-wrap"><table class="data-table compact"><thead><tr><th>Bot</th><th class="num">Real Score</th><th class="num">Closed</th><th class="num">Win Rate</th><th class="num">Avg P&L</th><th class="num">Seed Fit</th></tr></thead><tbody>{rows}</tbody></table></div>
  </div>
</div>"""

    # ── PAGE: MARKET REGIME ──────────────────────────────────────────────
    def _page_regime(self, regime_info):
        regime = regime_info.get("regime", "unknown")
        confidence = self._num(regime_info.get("confidence", 0))
        if confidence <= 1:
            confidence *= 100
        details = regime_info.get("details") or regime_info.get("stats", {})
        emojis = {"trending_up": "📈", "trending_down": "📉", "mean_reverting": "↔️",
                  "high_volatility": "⚡", "low_volatility": "⏸️", "choppy": "〰️"}
        emoji = emojis.get(regime, "❓")

        vol = self._fmt_metric(details.get("std_dev", details.get("volatility")), 2)
        trend = self._fmt_metric(details.get("mean_return", details.get("trend_direction")), 2)
        autocorr = self._fmt_metric(details.get("autocorrelation", details.get("support_strength")), 2)
        vol_ratio = self._fmt_metric(details.get("coefficient_of_variation", details.get("volatility_ratio")), 2)

        return f"""<div class="page" id="regime">
  <div class="page-title"><span class="accent">🌊</span> Market Regime <span class="data-badge">LIVE INTRADAY STATE</span></div>
  <p class="page-sub">This page should explain the market condition behind the trade gate, not just show a pretty chart. The symbol table updates from the intraday engine state.</p>
  <div class="regime-badge-large regime-{regime}">
    {emoji} Seed Baseline: {regime.replace('_',' ').title()} — {confidence:.0f}% Confidence
  </div>
  <div class="cards-row">
    <div class="card"><div class="card-label">Live Regimes</div><div class="card-value" id="regimeLiveCount" style="font-size:1.2em;">—</div><div class="card-sub">Symbols classified</div></div>
    <div class="card"><div class="card-label">Trending</div><div class="card-value" id="regimeTrending" style="font-size:1.2em;color:var(--lime);">—</div><div class="card-sub">Trend-friendly symbols</div></div>
    <div class="card"><div class="card-label">Choppy / Range</div><div class="card-value" id="regimeChoppy" style="font-size:1.2em;color:var(--amber);">—</div><div class="card-sub">Avoid trend entries or use range logic</div></div>
    <div class="card"><div class="card-label">High Vol Risk</div><div class="card-value" id="regimeHighVol" style="font-size:1.2em;color:var(--red);">—</div><div class="card-sub">Needs smaller size or no-trade gate</div></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Per-Symbol Regime Board</div><div class="section-sub">Use this to see whether each symbol is trend-following, range-bound, choppy, or too volatile right now.</div></div><span class="status-pill" id="regimeLiveStatus">Loading</span></div>
    <div id="regimeLiveEmpty" class="read-only-note">No live regime state has been saved yet.</div>
    <div id="regimeLiveTable" class="table-wrap" style="display:none;"><table class="data-table compact">
      <thead><tr><th>Symbol</th><th>Regime</th><th class="num">Confidence</th><th>Bias</th><th class="num">ATR %</th><th class="num">Volume</th><th>Why</th></tr></thead>
      <tbody id="regimeLiveBody"></tbody>
    </table></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Seed Regime Baseline</div><div class="section-sub">Legacy generated regime metrics kept only for context.</div></div></div>
    <div class="cards-row" style="margin-bottom:0;">
      <div class="card"><div class="card-label">Volatility</div><div class="card-value" style="font-size:1.2em;">{vol}</div><div class="card-sub">Generated baseline</div></div>
      <div class="card"><div class="card-label">Trend Direction</div><div class="card-value" style="font-size:1.2em;">{trend}</div><div class="card-sub">Generated baseline</div></div>
      <div class="card"><div class="card-label">Autocorrelation</div><div class="card-value" style="font-size:1.2em;">{autocorr}</div><div class="card-sub">Generated baseline</div></div>
      <div class="card"><div class="card-label">Vol Ratio</div><div class="card-value" style="font-size:1.2em;">{vol_ratio}</div><div class="card-sub">Generated baseline</div></div>
    </div>
  </div>
</div>"""

    # ── PAGE: DECISION LOG ───────────────────────────────────────────────
    def _page_decisions(self, evaluations):
        items = ""
        for bot_name, ed in evaluations.items():
            v = ed.get("verdict", "HOLD")
            base_v = ed.get("base_verdict", v)
            reasons = ed.get("reasons", [])
            reason_str = "; ".join(reasons) if isinstance(reasons, list) else str(reasons) if reasons else "—"
            is_override = base_v.upper() != v.upper()
            override_class = " override" if is_override else ""
            override_tag = f' <span style="color:var(--magenta);font-size:0.82em;">🔄 System changed from {base_v} → {v} (learning engine thinks this bot still has edge)</span>' if is_override else ""

            items += f"""<div class="decision-item{override_class}">
      <div class="decision-meta">
        <span class="decision-bot">{bot_name}</span>
        <span class="badge badge-{v.lower()}">{v}</span>{override_tag}
      </div>
      <div class="decision-reason">{reason_str}</div>
    </div>"""

        return f"""<div class="page" id="decisions">
  <div class="page-title"><span class="accent">📋</span> Decision Log <span class="data-badge">REAL PAPER JOURNAL</span></div>
  <p class="page-sub">This page is the audit trail: what opened, what closed, what was rejected, and the stated reason.</p>
  <div class="metric-grid">
    <div class="metric-card"><div class="metric-label">Recent Events</div><div class="metric-value" id="decEvents">—</div><div class="metric-sub">Latest journal window</div></div>
    <div class="metric-card"><div class="metric-label">Submitted</div><div class="metric-value" id="decSubmitted" style="color:var(--lime);">—</div><div class="metric-sub">Orders submitted</div></div>
    <div class="metric-card"><div class="metric-label">Rejected / Skipped</div><div class="metric-value" id="decRejected" style="color:var(--amber);">—</div><div class="metric-sub">Blocked by filters</div></div>
    <div class="metric-card"><div class="metric-label">Closed</div><div class="metric-value" id="decClosed">—</div><div class="metric-sub">Position exits</div></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Real Paper Decision Timeline</div><div class="section-sub">Alpaca paper decisions only. Seed/backtest events are not mixed into this table.</div></div><span class="status-pill" id="decLiveStatus">Loading</span></div>
    <div id="decEmpty" class="read-only-note">No real paper decision events yet.</div>
    <div id="decTable" class="table-wrap" style="display:none;"><table class="data-table compact">
      <thead><tr><th>Time</th><th>Symbol</th><th>Event</th><th>Strategy</th><th class="num">Confidence</th><th>Reason</th></tr></thead>
      <tbody id="decBody"></tbody>
    </table></div>
  </div>
  <div class="section-card">
    <div class="section-header"><div><div class="section-title">Seed Decision Baseline</div><div class="section-sub">Generated strategy verdicts kept below for background context only.</div></div></div>
    <div class="decision-timeline">{items}</div>
  </div>
</div>"""

    # ── JAVASCRIPT ───────────────────────────────────────────────────────
    def _scripts(self, evaluations, portfolio, regime_info):
        portfolio = portfolio or {}
        allocations = portfolio.get("allocations", [])

        # Prepare all data as JSON-safe Python, then dump once
        eval_names = list(evaluations.keys())

        # Portfolio chart data - use correct keys
        alloc_labels = [a.get("bot_name", a.get("strategy", "?"))[:18] for a in allocations]
        alloc_values = [a.get("allocation_usd", a.get("allocated_usd", 0)) for a in allocations]
        palette = ["#00d4ff","#39ff14","#ffb700","#ff006e","#ff4444","#00dd77",
                   "#a0d4ff","#88ff44","#ffcc00","#ff3388","#44bbff","#aaff14",
                   "#dd66ff","#66ffcc","#ff8844","#44ddff","#ccff33","#ff6699"]

        # Overview PnL chart
        ov_names = [n[:15] for n in eval_names]
        ov_pnl = [evaluations[n].get("net_profit", 0) for n in eval_names]
        ov_pnl_colors = ["#39ff14" if v >= 0 else "#ff4444" for v in ov_pnl]

        # Verdict pie
        vdict = {}
        for e in evaluations.values():
            v = e.get("verdict", "HOLD").upper()
            vdict[v] = vdict.get(v, 0) + 1
        verdict_labels = list(vdict.keys())
        verdict_values = list(vdict.values())
        verdict_colors = []
        color_map = {"PAUSE": "#ff4444", "HOLD": "#ffb700", "REACTIVATE": "#39ff14", "INSUFFICIENT_DATA": "#6b7394"}
        for vl in verdict_labels:
            verdict_colors.append(color_map.get(vl, "#00d4ff"))

        # Performance charts
        sorted_by_pnl = sorted(eval_names, key=lambda n: evaluations[n].get("net_profit", 0), reverse=True)
        top5 = sorted_by_pnl[:5]
        pnl_names = [n[:15] for n in top5]
        pnl_values = [evaluations[n].get("net_profit", 0) for n in top5]

        wr_names = [n[:12] for n in eval_names]
        wr_values = [evaluations[n].get("win_rate", 0) for n in eval_names]

        # Scatter: risk vs return
        scatter_data = [{"x": round(evaluations[n].get("sharpe_ratio", 0), 2),
                         "y": round(evaluations[n].get("profit_factor", 0), 2)}
                        for n in eval_names]

        # Radar for top strategy
        if eval_names:
            top = sorted_by_pnl[0]
            te = evaluations[top]
            radar_data = [
                round(te.get("win_rate", 0), 1),
                round(min(te.get("profit_factor", 0) * 30, 100), 1),
                round(min(te.get("sharpe_ratio", 0) * 50, 100), 1),
                round(max(100 - abs(te.get("max_drawdown", 0)) * 3, 0), 1),
                round(te.get("adaptation_score", 50), 1),
            ]
            radar_label = top[:20]
        else:
            radar_data = [50, 50, 50, 50, 50]
            radar_label = "No Data"

        # Adaptation chart
        adapt_names = [n[:12] for n in eval_names]
        adapt_scores = [evaluations[n].get("adaptation_score", 0) for n in eval_names]

        # Adapt vs Win Rate scatter
        adapt_wr_data = [{"x": evaluations[n].get("adaptation_score", 0),
                          "y": evaluations[n].get("win_rate", 0)}
                         for n in eval_names]

        # Regime chart: bar of adaptation scores colored by verdict
        regime_names = [n[:12] for n in eval_names]
        regime_scores = [evaluations[n].get("adaptation_score", 0) for n in eval_names]
        regime_bar_colors = []
        for n in eval_names:
            v = evaluations[n].get("verdict", "HOLD").upper()
            regime_bar_colors.append(color_map.get(v, "#00d4ff"))

        # Confidence radar from regime details
        rd = regime_info.get("details") or regime_info.get("stats", {})
        conf_labels = list(rd.keys())[:5] if rd else ["Confidence"]
        conf_values = []
        for k in conf_labels:
            val = rd.get(k, 0)
            try:
                num = float(val)
                conf_values.append(round(num * 100, 1) if 0 <= num <= 1 else round(num, 1))
            except (ValueError, TypeError):
                conf_values.append(50)  # default
        if not conf_labels:
            conf_labels = ["Regime Confidence"]
            base_conf = self._num(regime_info.get("confidence", 0))
            conf_values = [round(base_conf * 100, 1) if 0 <= base_conf <= 1 else round(base_conf, 1)]

        # Common chart options
        tt = '{"backgroundColor":"#0d1130","borderColor":"#2d3561","borderWidth":1,"titleColor":"#00d4ff","bodyColor":"#e0e6ff"}'

        return f"""
<script>
// ── Last Refresh Badge ─────────────────────────────────────────
function humanAgo(iso) {{
  try {{
    var t = parseUtcIso(iso).getTime();
    var secs = Math.floor((Date.now() - t) / 1000);
    if (secs < 60) return secs + 's ago';
    if (secs < 3600) return Math.floor(secs/60) + 'm ago';
    if (secs < 86400) return Math.floor(secs/3600) + 'h ago';
    return Math.floor(secs/86400) + 'd ago';
  }} catch (e) {{ return '?'; }}
}}

function parseUtcIso(iso) {{
  if (!iso) return new Date(NaN);
  var normalized = /([zZ]|[+-][0-9][0-9]:[0-9][0-9])$/.test(iso) ? iso : iso + 'Z';
  return new Date(normalized);
}}

function formatNyTime(iso) {{
  try {{
    return new Intl.DateTimeFormat('en-US', {{
      timeZone: 'America/New_York',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
      timeZoneName: 'short'
    }}).format(parseUtcIso(iso));
  }} catch (e) {{
    return '?';
  }}
}}

async function loadLastRefresh() {{
  var badge = document.getElementById('lastRefreshBadge');
  var timeEl = document.getElementById('lrTime');
  var trigEl = document.getElementById('lrTrigger');
  if (!badge || !timeEl) return;
  try {{
    var r = await fetch('/api/last-refresh');
    var d = await r.json();
    if (!d.refreshed) {{
      badge.className = 'last-refresh-badge missing';
      timeEl.textContent = 'Never refreshed';
      trigEl.style.display = 'none';
      return;
    }}
    var ageMins = (Date.now() - parseUtcIso(d.timestamp_utc).getTime()) / 60000;
    var stateClass = ageMins < 1440 ? 'fresh' : 'stale';
    badge.className = 'last-refresh-badge ' + stateClass;
    timeEl.innerHTML = (d.display_est || 'unknown') + ' <span style="color:var(--text-dim);font-family:Inter,sans-serif;font-size:0.85em;">(' + humanAgo(d.timestamp_utc) + ')</span>';
    var trig = (d.triggered_by || 'manual').toLowerCase();
    trigEl.textContent = trig === 'scheduled' ? '⏱ SCHEDULED' : '👤 MANUAL';
    trigEl.className = 'lr-trigger ' + (trig === 'scheduled' ? '' : 'manual');
    trigEl.style.display = '';
    badge.title = 'Triggered: ' + trig + ' | ' + (d.num_strategies || 0) + ' strategies | ' + (d.expected_monthly_return_pct || 0).toFixed(1) + '% expected';
  }} catch (e) {{
    badge.className = 'last-refresh-badge missing';
    timeEl.textContent = 'Dashboard static (run server for live data)';
    trigEl.style.display = 'none';
  }}
}}
// Refresh the badge on page load (periodic refresh handled by liveTick)
loadLastRefresh();

// ── Shared Live Alpaca Snapshot ────────────────────────────────
var _alpacaAccountCache = null;
var _alpacaAccountFetchedAt = 0;
var _alpacaAccountPromise = null;
var _alpacaPositionsPromise = null;
var _lastLivePositions = null;
var _alpacaConfiguredCache = null;

function money(n) {{
  return '$' + Number(n || 0).toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
}}

function setCardText(id, val, color) {{
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = val;
  if (color) el.style.color = color;
  autoSizeCardValue(el);
}}

async function isAlpacaConfigured() {{
  if (_alpacaConfiguredCache !== null) return _alpacaConfiguredCache;
  try {{
    var status = await apiGet('/api/alpaca/status');
    _alpacaConfiguredCache = !!status.configured;
  }} catch(e) {{
    _alpacaConfiguredCache = false;
  }}
  return _alpacaConfiguredCache;
}}

async function getAlpacaAccountCached(maxAgeMs) {{
  maxAgeMs = maxAgeMs === undefined ? 10000 : maxAgeMs;
  if (!(await isAlpacaConfigured())) throw new Error('Alpaca API keys are not configured');
  if (_alpacaAccountCache && (Date.now() - _alpacaAccountFetchedAt) < maxAgeMs) return _alpacaAccountCache;
  if (_alpacaAccountPromise) return _alpacaAccountPromise;
  _alpacaAccountPromise = apiGet('/api/alpaca/account').then(function(a) {{
    _alpacaAccountCache = a;
    _alpacaAccountFetchedAt = Date.now();
    _alpacaAccountPromise = null;
    return a;
  }}).catch(function(e) {{
    _alpacaAccountPromise = null;
    throw e;
  }});
  return _alpacaAccountPromise;
}}

async function getLivePositionsSnapshot() {{
  if (!(await isAlpacaConfigured())) {{
    return {{positions: [], summary: {{count: 0, total_market_value: 0, total_unrealized_pl: 0}}, configured: false}};
  }}
  if (_alpacaPositionsPromise) return _alpacaPositionsPromise;
  _alpacaPositionsPromise = apiGet('/api/alpaca/positions?live=1').then(function(pd) {{
    _lastLivePositions = pd;
    _alpacaPositionsPromise = null;
    return pd;
  }}).catch(function(e) {{
    _alpacaPositionsPromise = null;
    throw e;
  }});
  return _alpacaPositionsPromise;
}}

function liveAccountMetrics(acct, pd) {{
  acct = acct || {{}};
  pd = pd || {{}};
  var s = pd.summary || {{}};
  var cash = Number(acct.buying_power !== undefined ? acct.buying_power : acct.cash || 0);
  var positionValue = Number(s.total_market_value || 0);
  var liveEquity = positionValue > 0 ? cash + positionValue : Number(acct.equity || cash);
  var lastEquity = Number(acct.last_equity || liveEquity);
  var dayPL = liveEquity - lastEquity;
  var dayPLPct = lastEquity > 0 ? dayPL / lastEquity * 100 : 0;
  return {{
    cash: cash,
    liveEquity: liveEquity,
    lastEquity: lastEquity,
    dayPL: dayPL,
    dayPLPct: dayPLPct,
    openPL: Number(s.total_unrealized_pl || 0),
    openValue: positionValue,
    count: Number(s.count || (pd.positions || []).length || 0),
  }};
}}

function renderOverviewPositions(positions) {{
  var ovContainer = document.getElementById('ovPositionsTable');
  if (!ovContainer) return;
  positions = positions || [];
  if (!positions.length) {{
    ovContainer.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-dim);background:var(--card);border:1px solid var(--border);border-radius:12px;">No positions yet.</div>';
    return;
  }}
  var totalMV = 0;
  positions.forEach(function(p) {{ totalMV += Number(p.market_value || 0); }});
  var html = '<div class="table-wrap"><table class="data-table compact"><thead><tr><th>Symbol</th><th class="num">Live Price</th><th class="num">Market Value</th><th class="num">P&L</th><th class="num">% of Portfolio</th></tr></thead><tbody>';
  positions.forEach(function(p) {{
    var upl = Number(p.unrealized_pl || 0);
    var mv = Number(p.market_value || 0);
    var pct = totalMV > 0 ? (mv / totalMV * 100) : 0;
    var plColor = upl >= 0 ? 'var(--lime)' : 'var(--red)';
    html += '<tr>';
    html += '<td><strong>' + (p.symbol || '?') + '</strong></td>';
    html += '<td class="num">' + money(p.current_price) + '</td>';
    html += '<td class="num" style="color:var(--cyan);">' + money(mv) + '</td>';
    html += '<td class="num" style="color:' + plColor + ';font-weight:600;">' + (upl >= 0 ? '+' : '') + money(upl) + '</td>';
    html += '<td class="num">' + pct.toFixed(1) + '%</td>';
    html += '</tr>';
  }});
  html += '</tbody></table></div>';
  ovContainer.innerHTML = html;
}}

function renderPortfolioPositions(positions) {{
  var container = document.getElementById('pfPositionsTable');
  if (!container) return;
  positions = positions || [];
  if (!positions.length) {{
    container.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-dim);background:var(--card);border:1px solid var(--border);border-radius:12px;">No open positions. Use the Alpaca Trading page to execute trades.</div>';
    return;
  }}
  var totalMV = 0;
  positions.forEach(function(p) {{ totalMV += Number(p.market_value || 0); }});
  var html = '<div class="table-wrap"><table class="data-table compact"><thead><tr><th>Symbol</th><th class="num">Live Price</th><th class="num">Market Value</th><th class="num">P&L</th><th class="num">Allocation</th></tr></thead><tbody>';
  positions.forEach(function(p) {{
    var upl = Number(p.unrealized_pl || 0);
    var mv = Number(p.market_value || 0);
    var pct = totalMV > 0 ? (mv / totalMV * 100) : 0;
    var plColor = upl >= 0 ? 'var(--lime)' : 'var(--red)';
    var uplPct = Number(p.unrealized_plpc || 0);
    html += '<tr>';
    html += '<td><strong>' + (p.symbol || '?') + '</strong><span class="muted-line">Qty ' + Number(p.qty || 0).toFixed(4) + ' · Entry ' + money(p.avg_entry_price || p.cost_basis / (p.qty || 1)) + '</span></td>';
    html += '<td class="num">' + money(p.current_price) + '</td>';
    html += '<td class="num" style="color:var(--cyan);">' + money(mv) + '</td>';
    html += '<td class="num" style="color:' + plColor + ';font-weight:600;">' + (upl >= 0 ? '+' : '') + money(upl) + ' (' + (uplPct >= 0 ? '+' : '') + uplPct.toFixed(2) + '%)</td>';
    html += '<td class="num">' + pct.toFixed(1) + '%</td>';
    html += '</tr>';
  }});
  html += '</tbody></table></div>';
  container.innerHTML = html;
}}

function applyLiveSnapshotToOverview(acct, pd) {{
  var m = liveAccountMetrics(acct, pd);
  setCardText('ovEquity', money(m.liveEquity));
  setCardText('ovCash', money(m.cash));
  setCardText('ovStart', money(m.lastEquity));
  setCardText('ovPositions', String(m.count));
  var plColor = m.dayPL >= 0 ? 'var(--lime)' : 'var(--red)';
  setCardText('ovTotalPL', (m.dayPL >= 0 ? '+' : '') + money(m.dayPL), plColor);
  var plSub = document.getElementById('ovTotalPLsub');
  if (plSub) {{
    plSub.textContent = (m.dayPLPct >= 0 ? '+' : '') + m.dayPLPct.toFixed(2) + '% today · live quotes';
    plSub.style.color = plColor;
  }}
  renderOverviewPositions(pd.positions || []);
}}

function applyLiveSnapshotToPortfolio(acct, pd) {{
  var m = liveAccountMetrics(acct, pd);
  setCardText('pfEquity', money(m.liveEquity));
  setCardText('pfCash', money(m.cash));
  setCardText('pfPosCount', String(m.count));
  setCardText('pfPL', (m.openPL >= 0 ? '+' : '') + money(m.openPL), m.openPL >= 0 ? 'var(--lime)' : 'var(--red)');
  setCardText('pfDayPL', (m.dayPL >= 0 ? '+' : '') + money(m.dayPL), m.dayPL >= 0 ? 'var(--lime)' : 'var(--red)');
  var plSub = document.getElementById('pfPLsub');
  if (plSub) {{
    plSub.textContent = (m.dayPL >= 0 ? '+' : '') + money(m.dayPL) + ' (' + (m.dayPLPct >= 0 ? '+' : '') + m.dayPLPct.toFixed(2) + '%) today · live quotes';
    plSub.style.color = m.dayPL >= 0 ? 'var(--lime)' : 'var(--red)';
  }}
  renderPortfolioPositions(pd.positions || []);
}}

async function loadOverviewAccount(opts) {{
  opts = opts || {{}};
  try {{
    var acct = await getAlpacaAccountCached(opts.forceAccount ? 0 : 10000);
    var pd = opts.snapshot || await getLivePositionsSnapshot();
    applyLiveSnapshotToOverview(acct, pd);
  }} catch (e) {{
    try {{
      var d2 = await apiGet('/api/broker/connect');
      if (d2.connected && d2.account) {{
        setCardText('ovEquity', money(d2.account.equity));
        setCardText('ovCash', money(d2.account.cash));
        setCardText('ovStart', money(d2.account.starting_balance));
        setCardText('ovTotalPL', (d2.account.total_pl >= 0 ? '+' : '') + money(d2.account.total_pl), d2.account.total_pl >= 0 ? 'var(--lime)' : 'var(--red)');
      }}
    }} catch(e2) {{}}
  }}
  if (!opts.skipSlow) {{
    try {{
      var sr = await fetch('/api/status');
      var sd = await sr.json();
      if (sd.expected_monthly_return_pct !== undefined) {{
        setCardText('ovExpReturn', '+' + (sd.expected_monthly_return_pct || 0).toFixed(1) + '%');
      }}
    }} catch(e3) {{}}
  }}
}}
loadOverviewAccount({{forceAccount:true}});

// ── Portfolio page data from Alpaca ─────────────────────────────
async function loadPortfolioData(opts) {{
  opts = opts || {{}};
  try {{
    var acct = await getAlpacaAccountCached(opts.forceAccount ? 0 : 10000);
    var pd = opts.snapshot || await getLivePositionsSnapshot();
    applyLiveSnapshotToPortfolio(acct, pd);
  }} catch(e) {{}}
}}
loadPortfolioData({{forceAccount:true}});

// ── Navigation ──────────────────────────────────────────────────
var allPages = {json.dumps([p[0] for p in self.pages])};

var _currentPage = 'overview';

function showPage(name) {{
  _currentPage = name;
  allPages.forEach(function(p) {{
    var el = document.getElementById(p);
    if (el) el.classList.remove('active');
  }});
  var target = document.getElementById(name);
  if (target) target.classList.add('active');
  var navs = document.querySelectorAll('.nav-item');
  for (var i = 0; i < navs.length; i++) {{
    navs[i].classList.remove('active');
    if (navs[i].getAttribute('data-page') === name) navs[i].classList.add('active');
  }}
  setTimeout(function() {{ ensureChartsForPage(name); }}, 0);
  // Refresh data for the page being shown
  refreshPageData(name);
}}

// ── LIVE REFRESH ENGINE ─────────────────────────────────────────
// Refreshes the active page's data every 30s so numbers feel alive.
function refreshPageData(page) {{
  if (page === 'overview') {{
    loadOverviewAccount();
    loadLastRefresh();
    calLoadData();
  }} else if (page === 'alpaca-live') {{
    // Alpaca Live page
    if (alpLiveConnected) {{
      alpLiveRefreshPositions();
      alpLiveRefreshOrders();
      alpLiveRefreshJournal();
      alpLiveRefreshFeeAnalysis();
    }}
    alpAutoLoadStatus();
  }} else if (page === 'portfolio') {{
    loadPortfolioData();
    ganttLoad();
  }} else if (['quantum', 'bots', 'performance', 'learning', 'regime', 'decisions'].includes(page)) {{
    refreshInsightPage(page);
  }}
}}

async function updatePaperAccountFromAPI() {{
  try {{
    var data = await apiGet('/api/broker/account');
    if (data) updateAccountCards(data);
    var statusEl = document.getElementById('alpacaConnMsg');
    if (statusEl && data) {{
      var pl = data.total_pl || 0;
      var plStr = (pl >= 0 ? '+$' : '-$') + Math.abs(pl).toFixed(2);
      statusEl.textContent = 'Account ' + (data.account_number||'') + ' · Starting $' + (data.starting_balance || 1000).toFixed(2) + ' · Total P&L: ' + plStr;
    }}
  }} catch(e) {{}}
}}

// Global tick — 30s for heavier dashboard data, 1s fast-tick for Alpaca Live prices
var _liveTickCount = 0;
function liveTick() {{
  _liveTickCount++;
  refreshPageData(_currentPage);
}}
setInterval(liveTick, 30000);

// Fast tick — keep live account/portfolio numbers moving from the same quote snapshot
async function alpacaFastTick() {{
  if (!['overview', 'portfolio', 'alpaca-live'].includes(_currentPage)) return;
  try {{
    var snapshot = await getLivePositionsSnapshot();
    if (_currentPage === 'overview') {{
      await loadOverviewAccount({{skipSlow:true, snapshot:snapshot}});
    }} else if (_currentPage === 'portfolio') {{
      await loadPortfolioData({{snapshot:snapshot}});
    }} else if (_currentPage === 'alpaca-live' && alpLiveConnected) {{
      await alpLiveRefreshPositions({{skipAccount:true, snapshot:snapshot}});
    }}
  }} catch(e) {{}}
}}
setInterval(alpacaFastTick, 1000);

// ── Quantum Filter ──────────────────────────────────────────────
function filterQuantum(verdict) {{
  var table = document.getElementById('quantumTable');
  var rows = table.getElementsByTagName('tbody')[0].getElementsByTagName('tr');
  var btns = document.querySelectorAll('.filter-btn');
  for (var b = 0; b < btns.length; b++) btns[b].classList.remove('active');
  if (event && event.target) event.target.classList.add('active');
  for (var i = 0; i < rows.length; i++) {{
    if (verdict === 'ALL') {{
      rows[i].style.display = '';
    }} else {{
      rows[i].style.display = rows[i].getAttribute('data-verdict') === verdict ? '' : 'none';
    }}
  }}
}}

// ── Table Sort ──────────────────────────────────────────────────
var sortDir = {{}};
function sortTable(col) {{
  var table = document.getElementById('quantumTable');
  var tbody = table.getElementsByTagName('tbody')[0];
  var rows = Array.prototype.slice.call(tbody.getElementsByTagName('tr'));
  var asc = !sortDir[col];
  sortDir[col] = asc;
  rows.sort(function(a, b) {{
    var aText = a.getElementsByTagName('td')[col].textContent.trim();
    var bText = b.getElementsByTagName('td')[col].textContent.trim();
    var aNum = parseFloat(aText.replace(/[^0-9.-]/g, ''));
    var bNum = parseFloat(bText.replace(/[^0-9.-]/g, ''));
    if (!isNaN(aNum) && !isNaN(bNum)) return asc ? aNum - bNum : bNum - aNum;
    return asc ? aText.localeCompare(bText) : bText.localeCompare(aText);
  }});
  rows.forEach(function(r) {{ tbody.appendChild(r); }});
}}

// ── Operator Insight Pages ─────────────────────────────────────
var _insightCache = {{state:null, journal:null, ledger:null, learning:null, fetchedAt:0}};
var _insightInFlight = false;

function escHtml(txt) {{
  return String(txt === undefined || txt === null ? '' : txt)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}}

function shortText(txt, maxLen) {{
  txt = String(txt || '').trim();
  maxLen = maxLen || 160;
  return txt.length > maxLen ? txt.slice(0, maxLen) + '...' : txt;
}}

function pct(n) {{
  if (n === null || n === undefined || isNaN(Number(n))) return '—';
  return Number(n).toFixed(1) + '%';
}}

function signedMoney(n) {{
  n = Number(n || 0);
  return (n >= 0 ? '+' : '') + fmtUSD(n);
}}

function plColor(n) {{
  return Number(n || 0) >= 0 ? 'var(--lime)' : 'var(--red)';
}}

function setStatusPill(id, text, cls) {{
  var el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = 'status-pill ' + (cls || '');
}}

async function loadInsightData(force) {{
  if (!force && _insightCache.fetchedAt && (Date.now() - _insightCache.fetchedAt) < 5000) return _insightCache;
  if (_insightInFlight) return _insightCache;
  _insightInFlight = true;
  try {{
    var state = await apiGet('/api/intraday/state').catch(function() {{ return {{symbols: {{}}}}; }});
    var journal = await apiGet('/api/trade-journal?limit=200').catch(function() {{ return {{events: []}}; }});
    var ledger = await apiGet('/api/alpaca/trade-ledger?limit=500').catch(function() {{ return {{rows: [], summary: {{}}}}; }});
    var learning = await apiGet('/api/learning/live-status').catch(function() {{ return {{strategies: [], blocked_pairs: []}}; }});
    _insightCache = {{state: state, journal: journal, ledger: ledger, learning: learning, fetchedAt: Date.now()}};
  }} finally {{
    _insightInFlight = false;
  }}
  return _insightCache;
}}

function summarizeSignalReasons(symbols) {{
  var counts = {{}};
  (symbols || []).forEach(function(s) {{
    if (s && !s.accepted) {{
      var reason = String(s.reason || 'Unknown skip reason');
      var key = reason.split(';')[0].trim() || 'Unknown skip reason';
      counts[key] = (counts[key] || 0) + 1;
    }}
  }});
  var items = Object.keys(counts).map(function(k) {{ return {{reason:k, count:counts[k]}}; }});
  items.sort(function(a,b) {{ return b.count - a.count; }});
  return items;
}}

function summarizeGuardData(events) {{
  var now = new Date();
  var today = now.toISOString().slice(0, 10);
  var perSymbolBuys = {{}};
  var cooldownSymbols = {{}};
  var lossExits24h = 0;
  var exitReasonCounts = {{}};

  (events || []).forEach(function(e) {{
    var symbol = e.symbol || '';
    var ts = String(e.timestamp || e.closed_at || '');
    if (e.event === 'order_submitted' && e.side === 'buy' && symbol && ts.slice(0, 10) === today) {{
      perSymbolBuys[symbol] = (perSymbolBuys[symbol] || 0) + 1;
    }}
    if (e.event === 'position_closed') {{
      var plPct = Number(e.unrealized_pl_pct || 0);
      var dt = ts ? new Date(ts) : null;
      var hoursAgo = dt && !isNaN(dt.getTime()) ? ((now - dt) / 3600000) : null;
      if (hoursAgo !== null && hoursAgo <= 24 && plPct < 0) {{
        lossExits24h += 1;
      }}
      if (hoursAgo !== null && hoursAgo <= 3 && plPct < 0 && symbol) {{
        cooldownSymbols[symbol] = true;
      }}
      var reason = String(e.reason || 'Unknown exit');
      var key = 'Other';
      if (reason.indexOf('Stop loss') === 0) key = 'Stop loss';
      else if (reason.indexOf('Take profit') === 0) key = 'Take profit';
      else if (reason.indexOf('Trailing stop') === 0) key = 'Trailing stop';
      else if (reason.indexOf('Regime exit') === 0) key = 'Regime exit';
      else if (reason.indexOf('Early timeout') === 0) key = 'Early timeout';
      else if (reason.indexOf('Timeout exit') === 0) key = 'Timeout exit';
      else if (reason.indexOf('Stale position') === 0) key = 'Stale exit';
      exitReasonCounts[key] = (exitReasonCounts[key] || 0) + 1;
    }}
  }});

  var cappedSymbols = Object.keys(perSymbolBuys).filter(function(sym) {{ return perSymbolBuys[sym] >= 3; }});
  var exitRows = Object.keys(exitReasonCounts).map(function(k) {{ return {{reason:k, count:exitReasonCounts[k]}}; }});
  exitRows.sort(function(a,b) {{ return b.count - a.count; }});
  return {{
    cooldownSymbols: Object.keys(cooldownSymbols).sort(),
    cappedSymbols: cappedSymbols.sort(),
    lossExits24h: lossExits24h,
    exitRows: exitRows
  }};
}}

function groupLedger(rows, key) {{
  var out = {{}};
  (rows || []).forEach(function(r) {{
    var k = r[key] || 'unknown';
    if (!out[k]) out[k] = {{key:k, trades:0, wins:0, losses:0, net:0, fees:0}};
    var row = out[k];
    var net = Number(r.net_pl || 0);
    row.trades += 1;
    row.net += net;
    row.fees += Number(r.total_fees || 0);
    if (net > 0) row.wins += 1;
    if (net < 0) row.losses += 1;
  }});
  return Object.values(out).sort(function(a,b) {{ return b.net - a.net; }});
}}

function actionForGroup(g) {{
  var wr = g.trades ? g.wins / g.trades * 100 : 0;
  var avg = g.trades ? g.net / g.trades : 0;
  if (g.trades < 3) return {{label:'Collect more', cls:'warn', reason:'Too few closed trades for a real conclusion'}};
  if (g.net > 0 && wr >= 50 && avg > 0) return {{label:'Eligible boost', cls:'ok', reason:'Positive net after estimated fees'}};
  if (g.net < 0 && g.trades >= 3) return {{label:'Downweight', cls:'danger', reason:'Negative net paper result after estimated fees'}};
  return {{label:'Monitor', cls:'warn', reason:'Mixed evidence'}};
}}

async function renderStrategyScorecard(force) {{
  var data = await loadInsightData(force);
  var rows = (data.ledger && data.ledger.rows) || [];
  var groups = groupLedger(rows, 'strategy');
  var body = document.getElementById('scoreRealBody');
  var table = document.getElementById('scoreRealTable');
  var empty = document.getElementById('scoreRealEmpty');
  if (!body || !table || !empty) return;
  setStatusPill('scoreLiveStatus', rows.length ? 'Real ledger active' : 'Seed only', rows.length ? 'ok' : 'warn');
  setCardText('scoreRealStrategies', groups.length ? String(groups.length) : '0');
  var review = groups.filter(function(g) {{ return actionForGroup(g).label !== 'Eligible boost'; }}).length;
  setCardText('scoreNeedsReview', String(review));
  if (!groups.length) {{
    table.style.display = 'none';
    empty.style.display = 'block';
    setCardText('scoreBestStrategy', '—');
    var sub = document.getElementById('scoreBestStrategySub');
    if (sub) sub.textContent = 'Waiting for closed trades';
    return;
  }}
  empty.style.display = 'none';
  table.style.display = 'block';
  var best = groups[0];
  setCardText('scoreBestStrategy', best.key || 'unknown');
  var bestSub = document.getElementById('scoreBestStrategySub');
  if (bestSub) bestSub.textContent = signedMoney(best.net) + ' net after fees';
  body.innerHTML = groups.map(function(g) {{
    var wr = g.trades ? g.wins / g.trades * 100 : 0;
    var avg = g.trades ? g.net / g.trades : 0;
    var a = actionForGroup(g);
    return '<tr>' +
      '<td><strong>' + escHtml(g.key) + '</strong></td>' +
      '<td class="num">' + g.trades + '</td>' +
      '<td class="num" style="color:' + plColor(g.net) + ';font-weight:800;">' + signedMoney(g.net) + '</td>' +
      '<td class="num">' + pct(wr) + '</td>' +
      '<td class="num" style="color:' + plColor(avg) + ';">' + signedMoney(avg) + '</td>' +
      '<td><span class="status-pill ' + a.cls + '">' + a.label + '</span></td>' +
      '<td>' + escHtml(a.reason) + '</td>' +
      '</tr>';
  }}).join('');
}}

async function renderSignalBoard(force) {{
  var data = await loadInsightData(force);
  var symbols = Object.values((data.state && data.state.symbols) || {{}});
  var blockedPairs = (data.learning && data.learning.blocked_pairs) || [];
  var body = document.getElementById('signalsBody');
  var table = document.getElementById('signalsTable');
  var empty = document.getElementById('signalsEmpty');
  if (!body || !table || !empty) return;
  var tradable = symbols.filter(function(s) {{ return !!s.accepted; }}).length;
  var rejected = symbols.length - tradable;
  var topRejects = summarizeSignalReasons(symbols);
  setCardText('signalsSymbols', String(symbols.length));
  setCardText('signalsTradable', String(tradable));
  setCardText('signalsRejected', String(rejected));
  setCardText('signalsBlocked', String(blockedPairs.length), blockedPairs.length ? 'var(--red)' : null);
  setCardText('signalsTopReject', topRejects.length ? topRejects[0].reason : '—');
  setStatusPill('signalsLiveStatus', symbols.length ? 'Live state loaded' : 'No state yet', symbols.length ? 'ok' : 'warn');
  if (!symbols.length) {{
    table.style.display = 'none';
    empty.style.display = 'block';
    return;
  }}
  empty.style.display = 'none';
  table.style.display = 'block';
  symbols.sort(function(a,b) {{ return String(a.symbol || '').localeCompare(String(b.symbol || '')); }});
  body.innerHTML = symbols.map(function(s) {{
    var regime = (s.setup_regime && s.setup_regime.label) || (s.trade_regime && s.trade_regime.label) || 'unknown';
    var signals = s.strategy_signals || [];
    var top = signals.length ? signals.slice().sort(function(a,b) {{ return Number(b.confidence || 0) - Number(a.confidence || 0); }})[0] : {{}};
    var action = s.accepted ? (String(s.action || 'buy').toUpperCase()) : 'SKIP';
    var cls = s.accepted ? 'ok' : 'warn';
    return '<tr>' +
      '<td><strong>' + escHtml(s.symbol || '?') + '</strong><span class="muted-line">' + escHtml((s.evaluated_at || '').replace('T',' ').slice(0,19)) + '</span></td>' +
      '<td><span class="status-pill ' + cls + '">' + escHtml(action) + '</span></td>' +
      '<td class="num">' + Number(s.confidence || 0).toFixed(2) + '</td>' +
      '<td>' + escHtml(top.strategy || 'none') + '<span class="muted-line">' + escHtml(top.timeframe || '') + '</span></td>' +
      '<td>' + escHtml(regime) + '</td>' +
      '<td>' + escHtml(shortText(s.reason || 'No reason stored', 220)) + '</td>' +
      '</tr>';
  }}).join('');
}}

async function renderPerformancePage(force) {{
  var data = await loadInsightData(force);
  var rows = (data.ledger && data.ledger.rows) || [];
  var summary = (data.ledger && data.ledger.summary) || {{}};
  var fees = rows.reduce(function(acc, r) {{ return acc + Number(r.total_fees || 0); }}, 0);
  setCardText('perfTrades', String(summary.trades || rows.length || 0));
  setCardText('perfNet', signedMoney(summary.net_pl || 0), plColor(summary.net_pl || 0));
  setCardText('perfWinRate', summary.win_rate === null || summary.win_rate === undefined ? '—' : pct(summary.win_rate));
  setCardText('perfFees', fmtUSD(fees), 'var(--amber)');

  function fillGrouped(kind, key) {{
    var groups = groupLedger(rows, key);
    var table = document.getElementById('perf' + kind + 'Table');
    var empty = document.getElementById('perf' + kind + 'Empty');
    var body = document.getElementById('perf' + kind + 'Body');
    if (!table || !empty || !body) return;
    if (!groups.length) {{
      table.style.display = 'none';
      empty.style.display = 'block';
      return;
    }}
    empty.style.display = 'none';
    table.style.display = 'block';
    body.innerHTML = groups.map(function(g) {{
      var wr = g.trades ? g.wins / g.trades * 100 : 0;
      var avg = g.trades ? g.net / g.trades : 0;
      var read = actionForGroup(g);
      var maybeRead = kind === 'Strategy'
        ? '<td><span class="status-pill ' + read.cls + '">' + read.label + '</span></td>'
        : '';
      return '<tr>' +
        '<td><strong>' + escHtml(g.key) + '</strong></td>' +
        '<td class="num">' + g.trades + '</td>' +
        '<td class="num" style="color:' + plColor(g.net) + ';font-weight:800;">' + signedMoney(g.net) + '</td>' +
        '<td class="num">' + pct(wr) + '</td>' +
        '<td class="num" style="color:' + plColor(avg) + ';">' + signedMoney(avg) + '</td>' +
        '<td class="num" style="color:var(--amber);">' + fmtUSD(g.fees) + '</td>' +
        maybeRead +
        '</tr>';
    }}).join('');
  }}
  fillGrouped('Strategy', 'strategy');
  fillGrouped('Symbol', 'symbol');
}}

async function renderLearningPage(force) {{
  var data = await loadInsightData(force);
  var groups = groupLedger((data.ledger && data.ledger.rows) || [], 'strategy');
  var blockedPairs = (data.learning && data.learning.blocked_pairs) || [];
  var trusted = groups.filter(function(g) {{ return g.trades >= 10; }}).length;
  var collecting = groups.filter(function(g) {{ return g.trades < 10; }}).length;
  var down = groups.filter(function(g) {{ return actionForGroup(g).label === 'Downweight'; }}).length;
  setCardText('learnTrusted', String(trusted));
  setCardText('learnCollecting', String(collecting));
  setCardText('learnDownweight', String(down));
  setCardText('learnBlocked', String(blockedPairs.length), blockedPairs.length ? 'var(--red)' : null);
  setStatusPill('learnLiveStatus', groups.length ? 'Real ledger active' : 'Collecting', groups.length ? 'ok' : 'warn');
  var table = document.getElementById('learnRealTable');
  var empty = document.getElementById('learnRealEmpty');
  var body = document.getElementById('learnRealBody');
  if (!table || !empty || !body) return;
  if (!groups.length) {{
    table.style.display = 'none';
    empty.style.display = 'block';
    return;
  }}
  empty.style.display = 'none';
  table.style.display = 'block';
  body.innerHTML = groups.map(function(g) {{
    var wr = g.trades ? g.wins / g.trades * 100 : 0;
    var avg = g.trades ? g.net / g.trades : 0;
    var a = actionForGroup(g);
    return '<tr>' +
      '<td><strong>' + escHtml(g.key) + '</strong></td>' +
      '<td class="num">' + g.trades + '</td>' +
      '<td class="num" style="color:' + plColor(g.net) + ';font-weight:800;">' + signedMoney(g.net) + '</td>' +
      '<td class="num">' + pct(wr) + '</td>' +
      '<td class="num" style="color:' + plColor(avg) + ';">' + signedMoney(avg) + '</td>' +
      '<td><span class="status-pill ' + a.cls + '">' + a.label + '</span></td>' +
      '<td>' + escHtml(a.reason) + '</td>' +
      '</tr>';
  }}).join('');

  var blockTable = document.getElementById('learnBlockTable');
  var blockEmpty = document.getElementById('learnBlockEmpty');
  var blockBody = document.getElementById('learnBlockBody');
  if (blockTable && blockEmpty && blockBody) {{
    if (!blockedPairs.length) {{
      blockTable.style.display = 'none';
      blockEmpty.style.display = 'block';
    }} else {{
      blockEmpty.style.display = 'none';
      blockTable.style.display = 'block';
      blockBody.innerHTML = blockedPairs.map(function(row) {{
        return '<tr>' +
          '<td><strong>' + escHtml(row.strategy || 'unknown') + '</strong></td>' +
          '<td>' + escHtml(row.regime || 'unknown') + '</td>' +
          '<td class="num">' + Number(row.trades || 0) + '</td>' +
          '<td class="num">' + pct(row.win_rate || 0) + '</td>' +
          '<td class="num" style="color:' + plColor(row.pnl || 0) + ';">' + signedMoney(row.pnl || 0) + '</td>' +
          '<td>' + escHtml(shortText(row.reason || '', 180)) + '</td>' +
          '</tr>';
      }}).join('');
    }}
  }}
}}

async function renderRegimePage(force) {{
  var data = await loadInsightData(force);
  var symbols = Object.values((data.state && data.state.symbols) || {{}});
  var body = document.getElementById('regimeLiveBody');
  var table = document.getElementById('regimeLiveTable');
  var empty = document.getElementById('regimeLiveEmpty');
  if (!body || !table || !empty) return;
  var trending = 0, choppy = 0, highVol = 0;
  symbols.forEach(function(s) {{
    var rg = (s.setup_regime && s.setup_regime.label) || (s.trade_regime && s.trade_regime.label) || 'unknown';
    if (rg.indexOf('trending') >= 0) trending++;
    if (rg.indexOf('choppy') >= 0 || rg.indexOf('range') >= 0 || rg.indexOf('mean') >= 0) choppy++;
    var atr = Number((s.features && s.features.atr_pct_15m) || (s.trade_regime && s.trade_regime.atr_pct) || 0);
    if (rg.indexOf('high_vol') >= 0 || atr >= 8) highVol++;
  }});
  setCardText('regimeLiveCount', String(symbols.length));
  setCardText('regimeTrending', String(trending));
  setCardText('regimeChoppy', String(choppy));
  setCardText('regimeHighVol', String(highVol));
  setStatusPill('regimeLiveStatus', symbols.length ? 'Live state loaded' : 'No state yet', symbols.length ? 'ok' : 'warn');
  if (!symbols.length) {{
    table.style.display = 'none';
    empty.style.display = 'block';
    return;
  }}
  empty.style.display = 'none';
  table.style.display = 'block';
  symbols.sort(function(a,b) {{ return String(a.symbol || '').localeCompare(String(b.symbol || '')); }});
  body.innerHTML = symbols.map(function(s) {{
    var r = s.setup_regime || s.trade_regime || {{}};
    var tr = s.trade_regime || {{}};
    var conf = Number(r.confidence || s.confidence || 0);
    if (conf <= 1) conf *= 100;
    var atr = Number((s.features && s.features.atr_pct_15m) || tr.atr_pct || 0);
    var vol = Number(tr.volume_ratio || (s.features && s.features.volume_ratio_15m) || 0);
    return '<tr>' +
      '<td><strong>' + escHtml(s.symbol || '?') + '</strong><span class="muted-line">' + escHtml((s.evaluated_at || '').replace('T',' ').slice(0,19)) + '</span></td>' +
      '<td>' + escHtml(r.label || 'unknown') + '</td>' +
      '<td class="num">' + pct(conf) + '</td>' +
      '<td>' + escHtml(r.trend_bias || tr.trend_bias || 'unknown') + '</td>' +
      '<td class="num">' + atr.toFixed(2) + '%</td>' +
      '<td class="num">' + vol.toFixed(2) + 'x</td>' +
      '<td>' + escHtml(shortText(r.reason || s.reason || 'No regime reason stored', 220)) + '</td>' +
      '</tr>';
  }}).join('');
}}

async function renderDecisionPage(force) {{
  var data = await loadInsightData(force);
  var events = (data.journal && data.journal.events) || [];
  var submitted = events.filter(function(e) {{ return e.event === 'order_submitted'; }}).length;
  var rejected = events.filter(function(e) {{ return e.event === 'entry_rejected' || e.event === 'target_downweighted'; }}).length;
  var closed = events.filter(function(e) {{ return e.event === 'position_closed'; }}).length;
  setCardText('decEvents', String(events.length));
  setCardText('decSubmitted', String(submitted));
  setCardText('decRejected', String(rejected));
  setCardText('decClosed', String(closed));
  setStatusPill('decLiveStatus', events.length ? 'Real journal active' : 'No events', events.length ? 'ok' : 'warn');
  var table = document.getElementById('decTable');
  var empty = document.getElementById('decEmpty');
  var body = document.getElementById('decBody');
  if (!table || !empty || !body) return;
  if (!events.length) {{
    table.style.display = 'none';
    empty.style.display = 'block';
    return;
  }}
  empty.style.display = 'none';
  table.style.display = 'block';
  body.innerHTML = events.slice(0, 100).map(function(e) {{
    var event = e.event || 'event';
    var cls = event === 'order_submitted' ? 'ok' : event === 'position_closed' ? '' : 'warn';
    var conf = e.confidence !== undefined && e.confidence !== null ? Number(e.confidence).toFixed(2) : '—';
    var reason = e.reason || e.entry_reason || (e.signal && e.signal.reason) || '';
    return '<tr>' +
      '<td>' + escHtml((e.timestamp || '').replace('T',' ').slice(0,19)) + '</td>' +
      '<td><strong>' + escHtml(e.symbol || '—') + '</strong></td>' +
      '<td><span class="status-pill ' + cls + '">' + escHtml(event) + '</span></td>' +
      '<td>' + escHtml(e.strategy || '—') + '</td>' +
      '<td class="num">' + conf + '</td>' +
      '<td>' + escHtml(shortText(reason || 'No reason stored', 260)) + '</td>' +
      '</tr>';
  }}).join('');
}}

async function refreshInsightPage(page, force) {{
  if (page === 'quantum') await renderStrategyScorecard(force);
  else if (page === 'bots') await renderSignalBoard(force);
  else if (page === 'performance') await renderPerformancePage(force);
  else if (page === 'learning') await renderLearningPage(force);
  else if (page === 'regime') await renderRegimePage(force);
  else if (page === 'decisions') await renderDecisionPage(force);
}}

// ── Chart Tooltip Config ────────────────────────────────────────
var ttOpts = {{backgroundColor:'#0d1130',borderColor:'#2d3561',borderWidth:1,titleColor:'#00d4ff',bodyColor:'#e0e6ff',padding:10}};
var gridColor = '#2d3561';
var tickColor = '#8892b0';
var labelColor = '#e0e6ff';
var chartRegistry = [];
var chartInstances = {{}};

function chartSummaryRows(rows) {{
  return (rows || []).slice(0, 6);
}}

function renderChartFallback(canvas, rows) {{
  if (!canvas) return;
  var parent = canvas.parentNode;
  if (!parent) return;
  var existing = parent.querySelector('.chart-fallback');
  if (existing) return;
  canvas.style.display = 'none';
  var wrap = document.createElement('div');
  wrap.className = 'chart-fallback';
  var note = document.createElement('div');
  note.className = 'chart-fallback-note';
  note.textContent = 'Interactive charts are unavailable, so a compact data summary is shown instead.';
  wrap.appendChild(note);
  chartSummaryRows(rows).forEach(function(row) {{
    var item = document.createElement('div');
    item.className = 'chart-fallback-row';
    item.innerHTML = '<strong>' + row.label + '</strong><span>' + row.value + '</span>';
    wrap.appendChild(item);
  }});
  parent.appendChild(wrap);
}}

function registerChart(id, buildConfig, buildFallback) {{
  chartRegistry.push({{ id: id, buildConfig: buildConfig, buildFallback: buildFallback }});
}}

function ensureChartsForPage(name) {{
  chartRegistry.forEach(function(def) {{
    var canvas = document.getElementById(def.id);
    if (!canvas) return;
    var page = canvas.closest('.page');
    if (page && page.id !== name) return;
    if (chartInstances[def.id]) {{
      try {{
        chartInstances[def.id].resize();
        // Force redraw if canvas has zero dimensions
        var cw = canvas.clientWidth;
        var ch = canvas.clientHeight;
        if (cw > 0 && ch > 0) {{
          chartInstances[def.id].update();
        }}
      }} catch(e) {{}}
      return;
    }}
    if (typeof Chart === 'undefined') {{
      renderChartFallback(canvas, def.buildFallback());
      return;
    }}
    canvas.style.display = '';
    chartInstances[def.id] = new Chart(canvas, def.buildConfig());
  }});
}}

// ── 1. Overview P&L Bar ─────────────────────────────────────────
registerChart('overviewPnlChart', function() {{
  return {{
    type: 'bar',
    data: {{
      labels: {json.dumps(ov_names)},
      datasets: [{{ label: 'Net P&L ($)', data: {json.dumps(ov_pnl)}, backgroundColor: {json.dumps(ov_pnl_colors)}, borderWidth: 0 }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }}, tooltip: ttOpts }},
      scales: {{
        y: {{ ticks: {{ color: tickColor }}, grid: {{ color: gridColor }} }},
        x: {{ ticks: {{ color: tickColor, maxRotation: 45 }}, grid: {{ display: false }} }}
      }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": n, "value": f"${v:,.0f}"} for n, v in zip(ov_names, ov_pnl)])};
}});

// ── 2. Verdict Pie ──────────────────────────────────────────────
registerChart('verdictPieChart', function() {{
  return {{
    type: 'doughnut',
    data: {{
      labels: {json.dumps(verdict_labels)},
      datasets: [{{ data: {json.dumps(verdict_values)}, backgroundColor: {json.dumps(verdict_colors)}, borderColor: '#0a0e27', borderWidth: 2 }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ position: 'bottom', labels: {{ color: labelColor, padding: 16, font: {{ size: 12 }} }} }},
        tooltip: ttOpts
      }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": n, "value": str(v)} for n, v in zip(verdict_labels, verdict_values)])};
}});

// ── 4. P&L Line/Bar (Top 5) ────────────────────────────────────
registerChart('pnlChart', function() {{
  return {{
    type: 'bar',
    data: {{
      labels: {json.dumps(pnl_names)},
      datasets: [{{ label: 'Net P&L ($)', data: {json.dumps(pnl_values)}, backgroundColor: '#00d4ff', borderWidth: 0, borderRadius: 4 }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ labels: {{ color: labelColor }} }}, tooltip: ttOpts }},
      scales: {{
        y: {{ ticks: {{ color: tickColor }}, grid: {{ color: gridColor }} }},
        x: {{ ticks: {{ color: tickColor }}, grid: {{ display: false }} }}
      }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": n, "value": f"${v:,.0f}"} for n, v in zip(pnl_names, pnl_values)])};
}});

// ── 5. Win Rate Horizontal Bar ──────────────────────────────────
registerChart('winrateChart', function() {{
  return {{
    type: 'bar',
    data: {{
      labels: {json.dumps(wr_names)},
      datasets: [{{ label: 'Win Rate %', data: {json.dumps(wr_values)}, backgroundColor: '#39ff14', borderWidth: 0, borderRadius: 3 }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      plugins: {{ legend: {{ display: false }}, tooltip: ttOpts }},
      scales: {{
        x: {{ ticks: {{ color: tickColor }}, grid: {{ color: gridColor }}, max: 100 }},
        y: {{ ticks: {{ color: tickColor, font: {{ size: 10 }} }} }}
      }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": n, "value": f"{v:.1f}%"} for n, v in zip(wr_names, wr_values)])};
}});

// ── 6. Risk vs Return Scatter ───────────────────────────────────
registerChart('riskreturnChart', function() {{
  return {{
    type: 'scatter',
    data: {{
      datasets: [{{ label: 'Strategies', data: {json.dumps(scatter_data)}, backgroundColor: '#00d4ff', borderColor: '#39ff14', borderWidth: 2, pointRadius: 7 }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ labels: {{ color: labelColor }} }}, tooltip: ttOpts }},
      scales: {{
        x: {{ title: {{ display: true, text: 'Sharpe Ratio', color: labelColor }}, ticks: {{ color: tickColor }}, grid: {{ color: gridColor }} }},
        y: {{ title: {{ display: true, text: 'Profit Factor', color: labelColor }}, ticks: {{ color: tickColor }}, grid: {{ color: gridColor }} }}
      }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": d.get("label", "Strategy"), "value": f"Sharpe {d['x']:.2f} · PF {d['y']:.2f}"} for d in scatter_data])};
}});

// ── 7. Radar (Top Strategy) ────────────────────────────────────
registerChart('radarChart', function() {{
  return {{
    type: 'radar',
    data: {{
      labels: ['Win Rate', 'Profit Factor', 'Sharpe', 'Consistency', 'Adaptation'],
      datasets: [{{ label: {json.dumps(radar_label)}, data: {json.dumps(radar_data)}, borderColor: '#39ff14', backgroundColor: 'rgba(57,255,20,0.1)', borderWidth: 2, fill: true }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ labels: {{ color: labelColor }} }}, tooltip: ttOpts }},
      scales: {{ r: {{ ticks: {{ color: tickColor, backdropColor: 'transparent' }}, grid: {{ color: gridColor }}, beginAtZero: true, max: 100 }} }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": n, "value": f"{v:.1f}"} for n, v in zip(['Win Rate', 'Profit Factor', 'Sharpe', 'Consistency', 'Adaptation'], radar_data)])};
}});

// ── 8. Adaptation Bar ──────────────────────────────────────────
registerChart('adaptationChart', function() {{
  return {{
    type: 'bar',
    data: {{
      labels: {json.dumps(adapt_names)},
      datasets: [{{ label: 'Adaptation Score', data: {json.dumps(adapt_scores)}, backgroundColor: '#ffb700', borderWidth: 0, borderRadius: 3 }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ labels: {{ color: labelColor }} }}, tooltip: ttOpts }},
      scales: {{
        y: {{ ticks: {{ color: tickColor }}, grid: {{ color: gridColor }}, max: 100 }},
        x: {{ ticks: {{ color: tickColor, maxRotation: 45, font: {{ size: 10 }} }}, grid: {{ display: false }} }}
      }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": n, "value": f"{v:.0f}/100"} for n, v in zip(adapt_names, adapt_scores)])};
}});

// ── 9. Adaptation vs Win Rate Scatter ──────────────────────────
registerChart('adaptWinrateChart', function() {{
  return {{
    type: 'scatter',
    data: {{
      datasets: [{{ label: 'Adapt vs WR', data: {json.dumps(adapt_wr_data)}, backgroundColor: '#ff006e', borderColor: '#ffb700', borderWidth: 2, pointRadius: 7 }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ labels: {{ color: labelColor }} }}, tooltip: ttOpts }},
      scales: {{
        x: {{ title: {{ display: true, text: 'Adaptation Score', color: labelColor }}, ticks: {{ color: tickColor }}, grid: {{ color: gridColor }}, max: 100 }},
        y: {{ title: {{ display: true, text: 'Win Rate %', color: labelColor }}, ticks: {{ color: tickColor }}, grid: {{ color: gridColor }} }}
      }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": d.get("label", "Strategy"), "value": f"Adapt {d['x']:.0f} · WR {d['y']:.1f}%"} for d in adapt_wr_data])};
}});

// ── 10. Regime Bar Chart ───────────────────────────────────────
registerChart('regimeChart', function() {{
  return {{
    type: 'bar',
    data: {{
      labels: {json.dumps(regime_names)},
      datasets: [{{ label: 'Adaptation Score', data: {json.dumps(regime_scores)}, backgroundColor: {json.dumps(regime_bar_colors)}, borderWidth: 0, borderRadius: 3 }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ display: false }}, tooltip: ttOpts }},
      scales: {{
        y: {{ ticks: {{ color: tickColor }}, grid: {{ color: gridColor }}, max: 100 }},
        x: {{ ticks: {{ color: tickColor, maxRotation: 45, font: {{ size: 10 }} }}, grid: {{ display: false }} }}
      }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": n, "value": f"{v:.0f}/100"} for n, v in zip(regime_names, regime_scores)])};
}});

// ── 11. Confidence Radar ───────────────────────────────────────
registerChart('confidenceChart', function() {{
  return {{
    type: 'radar',
    data: {{
      labels: {json.dumps(conf_labels)},
      datasets: [{{ label: 'Regime Metrics', data: {json.dumps(conf_values)}, borderColor: '#00d4ff', backgroundColor: 'rgba(0,212,255,0.1)', borderWidth: 2, fill: true }}]
    }},
    options: {{
      responsive: true,
      plugins: {{ legend: {{ labels: {{ color: labelColor }} }}, tooltip: ttOpts }},
      scales: {{ r: {{ ticks: {{ color: tickColor, backdropColor: 'transparent' }}, grid: {{ color: gridColor }}, beginAtZero: true }} }}
    }}
  }};
}}, function() {{
  return {json.dumps([{"label": n, "value": f"{v:.2f}"} for n, v in zip(conf_labels, conf_values)])};
}});

// ── ALPACA PAGE ────────────────────────────────────────────────
var alpacaConnected = false;
var alpacaPreviewData = null;
var alpMsgTimer = null;

function alpMsg(text, color, persist) {{
  var el = document.getElementById('alpActionMsg');
  if (!el) return;
  if (alpMsgTimer) {{
    clearTimeout(alpMsgTimer);
    alpMsgTimer = null;
  }}
  el.style.display = 'block';
  el.style.color = color || 'var(--text-dim)';
  el.textContent = text;
  if (!persist) {{
    alpMsgTimer = setTimeout(function() {{
      el.style.display = 'none';
      el.textContent = '';
    }}, 8000);
  }}
}}

function fmtUSD(n) {{
  if (n === null || n === undefined || isNaN(n)) return '—';
  var val = Number(n);
  if (Math.abs(val) < 0.005) val = 0;  // avoid $-0.00
  var sign = val < 0 ? '-' : '';
  return sign + '$' + Math.abs(val).toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
}}
// Compact USD for large numbers in tight cards
function fmtUSDCompact(n) {{
  if (n === null || n === undefined || isNaN(n)) return '—';
  var val = Number(n);
  var abs = Math.abs(val);
  if (abs >= 1000000) return (val < 0 ? '-' : '') + '$' + (abs/1000000).toFixed(2) + 'M';
  if (abs >= 10000) return (val < 0 ? '-' : '') + '$' + (abs/1000).toFixed(1) + 'K';
  if (abs < 0.005) val = 0;
  return '$' + val.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
}}
// Auto-size: shrink font if text is too long for card
function autoSizeCardValue(el) {{
  if (!el) return;
  var len = (el.textContent || '').length;
  if (len > 12) el.style.fontSize = '1.6em';
  else if (len > 9) el.style.fontSize = '1.8em';
  else el.style.fontSize = '';  // reset to CSS default (2.2em)
}}

async function apiGet(url) {{
  try {{
    var r = await fetch(url);
    var j = await r.json();
    if (!r.ok) throw new Error(j.error || 'Request failed');
    return j;
  }} catch (e) {{
    throw e;
  }}
}}

async function apiPost(url, body) {{
  try {{
    var r = await fetch(url, {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(body || {{}}),
    }});
    var j = await r.json();
    if (!r.ok) throw new Error(j.error || 'Request failed');
    return j;
  }} catch (e) {{
    throw e;
  }}
}}

async function alpacaConnect() {{
  var statusEl = document.getElementById('alpacaConnStatus');
  var msgEl = document.getElementById('alpacaConnMsg');
  statusEl.style.color = 'var(--amber)';
  statusEl.textContent = '🟡 Connecting...';
  msgEl.textContent = 'Contacting Alpaca API...';
  try {{
    var data = await apiGet('/api/broker/connect');
    if (data.connected) {{
      alpacaConnected = true;
      statusEl.style.color = 'var(--lime)';
      statusEl.textContent = '🟢 Ready';
      var pl = data.account.total_pl || 0;
      var plStr = (pl >= 0 ? '+$' : '-$') + Math.abs(pl).toFixed(2);
      msgEl.textContent = 'Account ' + data.account.account_number + ' · Starting $' + (data.account.starting_balance || 1000).toFixed(2) + ' · Total P&L: ' + plStr;
      document.getElementById('alpacaAccountSection').style.display = 'block';
      updateAccountCards(data.account);
      alpacaLoadPositions();
      alpacaLoadOrders();
    }} else {{
      statusEl.style.color = 'var(--red)';
      statusEl.textContent = '🔴 Connection Failed';
      msgEl.textContent = data.error || 'Unknown error';
    }}
  }} catch (e) {{
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = '🔴 Connection Failed';
    msgEl.textContent = e.message;
  }}
}}

function updateAccountCards(acct) {{
  document.getElementById('alpEquity').textContent = fmtUSD(acct.equity);
  document.getElementById('alpCash').textContent = fmtUSD(acct.cash);
  document.getElementById('alpBP').textContent = fmtUSD(acct.buying_power);
  document.getElementById('alpAcctNum').textContent = acct.account_number || '—';
}}

async function alpacaLoadPositions() {{
  try {{
    var data = await apiGet('/api/broker/positions');
    var body = document.getElementById('alpPositionsBody');
    document.getElementById('alpPosCount').textContent = data.summary.count;
    document.getElementById('alpPL').textContent = fmtUSD(data.summary.total_unrealized_pl);
    document.getElementById('alpPL').style.color = data.summary.total_unrealized_pl >= 0 ? 'var(--lime)' : 'var(--red)';

    if (!data.positions || data.positions.length === 0) {{
      body.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-dim);background:var(--card);border:1px solid var(--border);border-radius:12px;">No positions yet. Click "Execute Paper Orders" to open positions.</div>';
      return;
    }}
    var rows = '';
    data.positions.forEach(function(p) {{
      var plColor = p.unrealized_pl >= 0 ? 'var(--lime)' : 'var(--red)';
      rows += '<tr>' +
        '<td><strong>' + p.symbol + '</strong></td>' +
        '<td>' + Number(p.qty).toFixed(6) + '</td>' +
        '<td>' + fmtUSD(p.avg_entry_price) + '</td>' +
        '<td>' + fmtUSD(p.current_price) + '</td>' +
        '<td>' + fmtUSD(p.market_value) + '</td>' +
        '<td style="color:' + plColor + ';font-weight:700;">' + fmtUSD(p.unrealized_pl) + '</td>' +
        '<td style="color:' + plColor + ';font-weight:700;">' + (p.unrealized_plpc >= 0 ? '+' : '') + Number(p.unrealized_plpc).toFixed(2) + '%</td>' +
        '</tr>';
    }});
    body.innerHTML = '<div class="table-wrap"><table class="data-table compact"><thead><tr>' +
      '<th>Symbol</th><th>Qty</th><th>Avg Entry</th><th>Current</th>' +
      '<th>Market Value</th><th>Unrealized P&L</th><th>%</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }} catch (e) {{
    alpMsg('Failed to load positions: ' + e.message, 'var(--red)');
  }}
}}

async function alpacaLoadOrders() {{
  try {{
    var data = await apiGet('/api/broker/orders?limit=20');
    var body = document.getElementById('alpOrdersBody');
    if (!data.orders || data.orders.length === 0) {{
      body.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-dim);background:var(--card);border:1px solid var(--border);border-radius:12px;">No orders yet.</div>';
      return;
    }}
    var rows = '';
    data.orders.forEach(function(o) {{
      var sideColor = o.side === 'buy' ? 'var(--lime)' : 'var(--amber)';
      rows += '<tr data-side="' + (o.side || '').toLowerCase() + '">' +
        '<td>' + (o.submitted_at || '').substring(0, 19).replace('T', ' ') + '</td>' +
        '<td><strong>' + o.symbol + '</strong></td>' +
        '<td style="color:' + sideColor + ';font-weight:700;">' + (o.side || '').toUpperCase() + '</td>' +
        '<td>' + (o.notional ? fmtUSD(o.notional) : (o.qty ? Number(o.qty).toFixed(6) : '—')) + '</td>' +
        '<td>' + (o.filled_avg_price ? fmtUSD(o.filled_avg_price) : '—') + '</td>' +
        '<td><span class="badge" style="background:rgba(0,212,255,0.15);color:var(--cyan);">' + o.status + '</span></td>' +
        '</tr>';
    }});
    body.innerHTML = '<div class="table-wrap"><table class="data-table compact"><thead><tr>' +
      '<th>Time</th><th>Symbol</th><th>Side</th><th>Notional/Qty</th>' +
      '<th>Fill Price</th><th>Status</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  }} catch (e) {{
    alpMsg('Failed to load orders: ' + e.message, 'var(--red)');
  }}
}}

function filterOrders(side) {{
  var btns = [document.getElementById('orderFilterAll'), document.getElementById('orderFilterBuy'), document.getElementById('orderFilterSell')];
  btns.forEach(function(b) {{ if (b) b.classList.remove('active'); }});
  var activeBtn = document.getElementById('orderFilter' + side.charAt(0).toUpperCase() + side.slice(1));
  if (activeBtn) activeBtn.classList.add('active');
  var body = document.getElementById('alpOrdersBody');
  var rows = body.querySelectorAll('tr[data-side]');
  for (var i = 0; i < rows.length; i++) {{
    if (side === 'all') {{
      rows[i].style.display = '';
    }} else {{
      rows[i].style.display = rows[i].getAttribute('data-side') === side ? '' : 'none';
    }}
  }}
}}

async function alpacaRefreshDailyRun() {{
  alpMsg('Running daily analysis (this takes ~5-10 seconds)...', 'var(--cyan)');
  try {{
    var data = await apiPost('/api/daily-run');
    if (data.ok) {{
      alpMsg('✅ Analysis complete. Reloading dashboard...', 'var(--lime)');
      setTimeout(function() {{ window.location.reload(); }}, 1500);
    }} else {{
      alpMsg('Analysis failed. Check terminal output.', 'var(--red)');
    }}
  }} catch (e) {{
    alpMsg('Failed: ' + e.message, 'var(--red)');
  }}
}}

async function alpacaPreview() {{
  alpMsg('Computing order preview...', 'var(--cyan)');
  try {{
    var data = await apiGet('/api/broker/preview');
    alpacaPreviewData = data;
    var section = document.getElementById('alpPreviewSection');
    section.style.display = 'block';
    var s = data.summary || {{}};
    document.getElementById('alpPreviewSummary').innerHTML =
      '<strong>' + (s.total_orders || 0) + '</strong> orders will be placed · ' +
      (s.buys || 0) + ' buys, ' + (s.sells || 0) + ' sells, ' + (s.closes || 0) + ' closes · ' +
      'Deploying <strong>' + fmtUSD(s.total_capital_deployed_usd || 0) + '</strong> · ' +
      '<span style="color:var(--amber);">' + (s.skipped || 0) + ' skipped</span>';

    var rows = '';
    (data.orders || []).forEach(function(o) {{
      var sideColor = o.side === 'buy' ? 'var(--lime)' : (o.side === 'sell' ? 'var(--amber)' : 'var(--red)');
      rows += '<tr>' +
        '<td>' + (o.bot || '—') + '</td>' +
        '<td><strong>' + (o.symbol || '—') + '</strong></td>' +
        '<td style="color:' + sideColor + ';font-weight:700;">' + (o.side || '—').toUpperCase() + '</td>' +
        '<td>' + fmtUSD(o.notional) + '</td>' +
        '<td>' + fmtUSD(o.target_usd) + '</td>' +
        '<td>' + fmtUSD(o.current_usd) + '</td>' +
        '<td><span class="badge" style="background:rgba(255,183,0,0.15);color:var(--amber);">DRY RUN</span></td>' +
        '</tr>';
    }});
    document.getElementById('alpPreviewBody').innerHTML = rows || '<tr><td colspan="7" style="text-align:center;color:var(--text-dim);padding:20px;">No orders to place</td></tr>';

    var skipHtml = '';
    if (data.skipped && data.skipped.length > 0) {{
      skipHtml = '<div style="margin-top:12px;padding:16px;background:rgba(255,183,0,0.05);border:1px solid var(--amber);border-radius:8px;">' +
        '<strong style="color:var(--amber);">Skipped (' + data.skipped.length + '):</strong><ul style="margin:8px 0 0 20px;color:var(--text-dim);font-size:0.88em;">';
      data.skipped.slice(0, 10).forEach(function(s) {{
        skipHtml += '<li>' + (s.bot || '—') + ' (' + (s.pair || '') + '): ' + s.reason + '</li>';
      }});
      if (data.skipped.length > 10) skipHtml += '<li>... and ' + (data.skipped.length - 10) + ' more</li>';
      skipHtml += '</ul></div>';
    }}
    document.getElementById('alpPreviewSkipped').innerHTML = skipHtml;
    alpMsg('✅ Preview ready. Review orders, then click "Execute Paper Orders" to place them.', 'var(--lime)');
  }} catch (e) {{
    alpMsg('Preview failed: ' + e.message, 'var(--red)');
  }}
}}

function alpModalShow(title, body, confirmText, onConfirm) {{
  document.getElementById('alpModalTitle').textContent = title;
  document.getElementById('alpModalBody').innerHTML = body;
  var btn = document.getElementById('alpModalConfirmBtn');
  btn.textContent = confirmText;
  btn.onclick = function() {{ alpModalCancel(); onConfirm(); }};
  var modal = document.getElementById('alpModal');
  modal.style.display = 'flex';
}}

function alpModalCancel() {{
  document.getElementById('alpModal').style.display = 'none';
}}

function alpacaConfirmExecute() {{
  if (!alpacaPreviewData) {{
    alpMsg('⚠️ Run "Preview Orders" first so you know what will be placed.', 'var(--amber)');
    return;
  }}
  var s = alpacaPreviewData.summary || {{}};
  var body = 'This will place <strong>' + (s.total_orders || 0) + '</strong> paper orders on Alpaca:<br><br>' +
    '• ' + (s.buys || 0) + ' buys<br>' +
    '• ' + (s.sells || 0) + ' sells<br>' +
    '• ' + (s.closes || 0) + ' closes<br>' +
    '• Deploying ' + fmtUSD(s.total_capital_deployed_usd || 0) + '<br><br>' +
    '<span style="color:var(--amber);">Paper trading only — no real money involved.</span>';
  alpModalShow('Execute Paper Orders?', body, '▶️ Execute', alpacaExecute);
}}

async function alpacaExecute() {{
  alpMsg('Placing orders on Alpaca... this may take a few seconds...', 'var(--cyan)');
  try {{
    var data = await apiPost('/api/broker/execute', {{confirm: true}});
    var s = data.summary || {{}};
    alpMsg('✅ Executed ' + (s.total_orders || 0) + ' orders. Refreshing positions...', 'var(--lime)');
    // Refresh everything
    setTimeout(function() {{
      alpacaConnect();
    }}, 2000);
  }} catch (e) {{
    alpMsg('Execute failed: ' + e.message, 'var(--red)');
  }}
}}

function alpacaConfirmCloseAll() {{
  var body = '<strong style="color:var(--red);">This will close ALL open positions immediately.</strong><br><br>' +
    'This is useful if you want to start fresh. No real money is involved (paper trading).';
  alpModalShow('Close All Positions?', body, '⛔ Close All', alpacaCloseAll);
}}

async function alpacaCloseAll() {{
  alpMsg('Closing all positions...', 'var(--cyan)');
  try {{
    await apiPost('/api/broker/close-all', {{confirm: true}});
    alpMsg('✅ Close orders submitted. Refreshing...', 'var(--lime)');
    setTimeout(function() {{ alpacaConnect(); }}, 2000);
  }} catch (e) {{
    alpMsg('Close all failed: ' + e.message, 'var(--red)');
  }}
}}

function alpacaConfirmReset() {{
  var body = '<strong style="color:var(--amber);">This will wipe all positions and orders</strong> and restore the simulator to <strong>$1,000</strong> starting cash.<br><br>' +
    'Use this any time you want to start the paper account fresh.';
  alpModalShow('Reset Paper Account?', body, '🔄 Reset', alpacaResetAccount);
}}

async function alpacaResetAccount() {{
  alpMsg('Resetting simulator to $1,000...', 'var(--cyan)');
  try {{
    await apiPost('/api/broker/reset', {{confirm: true, starting_balance: 1000}});
    alpMsg('✅ Account reset. Refreshing...', 'var(--lime)');
    setTimeout(function() {{ alpacaConnect(); }}, 1500);
  }} catch (e) {{
    alpMsg('Reset failed: ' + e.message, 'var(--red)');
  }}
}}

function escapeHtml(value) {{
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}}

// ── AUTO-TRADING ───────────────────────────────────────────────
async function autoRefresh() {{
  try {{
    var s = await apiGet('/api/auto/status');
    var badge = document.getElementById('autoStateBadge');
    var btn = document.getElementById('autoToggleBtn');
    if (!badge || !btn) return;
    if (s.enabled) {{
      badge.textContent = 'ON';
      badge.style.background = 'rgba(57,255,20,0.15)';
      badge.style.color = 'var(--lime)';
      btn.textContent = '⏸️ Disable Auto-Trading';
      btn.style.background = 'rgba(255,68,68,0.1)';
      btn.style.color = 'var(--red)';
      btn.style.borderColor = 'var(--red)';
    }} else {{
      badge.textContent = 'OFF';
      badge.style.background = 'rgba(107,115,148,0.15)';
      badge.style.color = 'var(--gray)';
      btn.textContent = '▶️ Enable Auto-Trading';
      btn.style.background = 'rgba(57,255,20,0.15)';
      btn.style.color = 'var(--lime)';
      btn.style.borderColor = 'var(--lime)';
    }}
    document.getElementById('autoInterval').textContent = (s.interval_min || 15) + ' min';
    document.getElementById('autoLastRun').textContent = s.last_run ? formatNyTime(s.last_run) + ' (' + humanAgo(s.last_run) + ')' : '—';
    document.getElementById('autoNextRun').textContent = s.next_run ? formatNyTime(s.next_run) : '—';
    var lr = s.last_result;
    if (lr) {{
      var color = lr.status === 'ok' ? 'var(--lime)' : 'var(--red)';
      var detail = lr.error ? '<div style="font-size:0.72em;color:var(--text-dim);margin-top:6px;text-transform:none;letter-spacing:0;">' + escapeHtml(String(lr.error)) + '</div>' : '';
      document.getElementById('autoLastResult').innerHTML = '<span style="color:' + color + ';">' + lr.status.toUpperCase() + '</span>' + detail;
    }}
    // Recent runs log
    var runs = s.recent_runs || [];
    if (runs.length) {{
      var logHtml = '<div style="font-size:0.72em;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">Recent auto-runs</div>';
      logHtml += '<div style="max-height:160px;overflow:auto;background:rgba(10,14,39,0.3);border-radius:8px;padding:10px;font-family:Courier New,monospace;font-size:0.78em;">';
      runs.slice().reverse().forEach(function(r) {{
        var statusColor = r.status === 'ok' ? 'var(--lime)' : r.status === 'running' ? 'var(--cyan)' : 'var(--red)';
        var time = r.timestamp ? formatNyTime(r.timestamp) : '?';
        var summary = '';
        if (r.steps && r.steps.trade && r.steps.trade.summary) {{
          var s2 = r.steps.trade.summary;
          summary = ' · ' + (s2.buys || 0) + ' buys, ' + (s2.sells || 0) + ' sells, ' + (s2.closes || 0) + ' closes · equity $' + (r.steps.trade.equity_after || 0).toFixed(2);
        }}
        if (!summary && r.error) {{
          summary = ' · ' + escapeHtml(String(r.error));
        }}
        logHtml += '<div style="padding:3px 0;color:var(--text-dim);">' + time + ' <span style="color:' + statusColor + ';">' + r.status.toUpperCase() + '</span>' + summary + '</div>';
      }});
      logHtml += '</div>';
      document.getElementById('autoRunsLog').innerHTML = logHtml;
    }}
  }} catch (e) {{ /* silent — server not running */ }}
}}

async function autoToggle() {{
  try {{
    var s = await apiGet('/api/auto/status');
    var newState = !s.enabled;
    await apiPost('/api/auto/toggle', {{enabled: newState}});
    alpMsg(newState ? '🤖 Auto-trading ENABLED — first cycle will run within 15 min' : '⏸️ Auto-trading DISABLED',
           newState ? 'var(--lime)' : 'var(--amber)');
    autoRefresh();
  }} catch (e) {{
    alpMsg('Toggle failed: ' + e.message, 'var(--red)');
  }}
}}

async function autoRunNow() {{
  try {{
    await apiPost('/api/auto/run-now', {{confirm: true}});
    alpMsg('⚡ Manual cycle triggered (runs in background, refresh in ~30-60s)', 'var(--cyan)');
    setTimeout(autoRefresh, 3000);
  }} catch (e) {{
    alpMsg('Manual trigger failed: ' + e.message, 'var(--red)');
  }}
}}

// Initial auto-refresh load (periodic refresh handled by liveTick)
autoRefresh();

// Auto-connect brokers and set up page navigation
var alpacaAutoTried = false;
document.addEventListener('DOMContentLoaded', function() {{
  var initialPage = document.querySelector('.page.active');
  var initialPageId = initialPage ? initialPage.id : 'overview';
  _currentPage = initialPageId;
  ensureChartsForPage(initialPageId);
  setTimeout(function() {{ ensureChartsForPage(initialPageId); }}, 200);
  setTimeout(function() {{ ensureChartsForPage(initialPageId); }}, 800);

  // Auto-connect Alpaca Live on page load (if keys are configured)
  setTimeout(function() {{
    alpLiveAutoReconnect();
  }}, 1500);
}});

// Alpaca Live: auto-reconnect on page load so user doesn't have to click Connect every refresh
async function alpLiveAutoReconnect() {{
  try {{
    var data = await apiGet('/api/alpaca/status');
    if (data.configured) {{
      // Keys are set — auto-connect silently
      var statusEl = document.getElementById('alpLiveConnStatus');
      var msgEl = document.getElementById('alpLiveConnMsg');
      if (statusEl) {{
        statusEl.textContent = '🟡 Auto-connecting...';
        statusEl.style.color = 'var(--amber)';
      }}
      var connData = await apiPost('/api/alpaca/connect', {{}});
      if (connData.connected) {{
        alpLiveConnected = true;
        if (statusEl) {{
          statusEl.textContent = '🟢 Connected';
          statusEl.style.color = 'var(--lime)';
        }}
        if (msgEl) {{
          msgEl.textContent = 'Account ' + (connData.account.account_number || '') + ' · ' + (connData.account.status || '');
        }}
        var btn = document.getElementById('alpLiveConnBtn');
        if (btn) {{
          btn.textContent = '✅ Connected';
          btn.style.borderColor = 'var(--lime)';
          btn.style.color = 'var(--lime)';
          btn.disabled = true;
        }}
        document.getElementById('alpLiveAccountSection').style.display = 'block';
        document.getElementById('alpLiveNotConfigured').style.display = 'none';
        document.getElementById('alpLiveConnCard').style.display = 'block';
        alpLiveUpdateAccount(connData.account);
        alpLiveRefreshPositions();
        alpLiveRefreshOrders();
        alpLiveRefreshFeeAnalysis();
        alpAutoLoadStatus();
      }}
    }}
  }} catch(e) {{
    // Silent — will show "Not Connected" until user clicks Connect
  }}
}}

window.addEventListener('resize', function() {{
  var activePage = document.querySelector('.page.active');
  if (activePage) ensureChartsForPage(activePage.id);
}});

// ── GANTT BOT TIMELINE ────────────────────────────────────────
var ganttColors = ['#00d4ff','#39ff14','#ffb700','#ff006e','#ff4444','#00dd77','#6b7394','#e0e6ff','#00d4ff','#39ff14','#ffb700','#ff006e'];

function ganttSetDefaultDates() {{
  var now = new Date();
  var from = new Date(now.getFullYear(), now.getMonth(), 1);
  document.getElementById('ganttFrom').value = from.toISOString().split('T')[0];
  document.getElementById('ganttTo').value = now.toISOString().split('T')[0];
}}

async function ganttLoad() {{
  try {{
    var data = await apiGet((await isAlpacaConfigured()) ? '/api/alpaca/orders?limit=500&status=closed' : '/api/broker/orders?limit=500');
    if (!data.orders || data.orders.length === 0) return;
    window._ganttOrders = data.orders;
    ganttRender();
  }} catch(e) {{}}
}}

function ganttApplyDates() {{ ganttRender(); }}

function ganttRender() {{
  var orders = window._ganttOrders || [];
  if (!orders.length) return;
  var fromStr = document.getElementById('ganttFrom').value;
  var toStr = document.getElementById('ganttTo').value;
  var fromDate = fromStr ? new Date(fromStr + 'T00:00:00') : new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  var toDate = toStr ? new Date(toStr + 'T23:59:59') : new Date();
  var rangeMs = toDate - fromDate;
  if (rangeMs <= 0) return;

  // Group orders by symbol, build activity spans
  var symbols = {{}};
  orders.forEach(function(o) {{
    if (!o.symbol || (!o.filled_at && !o.submitted_at)) return;
    if ((o.filled_qty || 0) <= 0) return;
    if (!symbols[o.symbol]) symbols[o.symbol] = [];
    symbols[o.symbol].push(o);
  }});

  // Build spans: buy opens a span, sell closes it
  var spans = {{}};
  Object.keys(symbols).forEach(function(sym) {{
    var symOrders = symbols[sym].sort(function(a,b) {{
      var at = a.filled_at || a.submitted_at || '';
      var bt = b.filled_at || b.submitted_at || '';
      return at.localeCompare(bt);
    }});
    spans[sym] = [];
    var currentSpan = null;
    symOrders.forEach(function(o) {{
      var t = new Date(o.filled_at || o.submitted_at);
      var oSide = (o.side || '').toLowerCase().split('.').pop();
      if (oSide === 'buy') {{
        if (!currentSpan) {{
          currentSpan = {{ start: t, entryPrice: o.filled_avg_price || 0, symbol: sym }};
        }}
      }} else if (oSide === 'sell' && currentSpan) {{
        currentSpan.end = t;
        currentSpan.exitPrice = o.filled_avg_price || 0;
        spans[sym].push(currentSpan);
        currentSpan = null;
      }}
    }});
    if (currentSpan) {{
      currentSpan.end = new Date(); // still open
      currentSpan.exitPrice = null; // still active
      spans[sym].push(currentSpan);
    }}
  }});

  var container = document.getElementById('ganttContainer');
  var html = '';
  var symKeys = Object.keys(spans).sort();
  symKeys.forEach(function(sym, idx) {{
    var color = ganttColors[idx % ganttColors.length];
    html += '<div class="gantt-row"><div class="gantt-label">' + sym + '</div><div class="gantt-track">';
    spans[sym].forEach(function(sp) {{
      var startMs = Math.max(sp.start - fromDate, 0);
      var endMs = Math.min(sp.end - fromDate, rangeMs);
      if (endMs <= 0 || startMs >= rangeMs) return;
      var leftPct = (startMs / rangeMs * 100).toFixed(2);
      var widthPct = Math.max(((endMs - startMs) / rangeMs * 100), 0.5).toFixed(2);
      var entryStr = sp.entryPrice ? '$' + Number(sp.entryPrice).toFixed(2) : '—';
      var exitStr = sp.exitPrice === null ? 'Still Open' : (sp.exitPrice ? '$' + Number(sp.exitPrice).toFixed(2) : '—');
      var dateRange = sp.start.toLocaleDateString() + ' → ' + (sp.exitPrice === null ? 'Now' : sp.end.toLocaleDateString());
      html += '<div class="gantt-bar" style="left:' + leftPct + '%;width:' + widthPct + '%;background:' + color + ';">' +
        '<div class="gantt-tooltip">' + sym + '<br>Entry: ' + entryStr + '<br>Exit: ' + exitStr + '<br>' + dateRange + '</div></div>';
    }});
    html += '</div></div>';
  }});

  // Axis labels
  var axisCount = 6;
  html += '<div class="gantt-axis">';
  for (var a = 0; a <= axisCount; a++) {{
    var d = new Date(fromDate.getTime() + (rangeMs * a / axisCount));
    html += '<span>' + (d.getMonth()+1) + '/' + d.getDate() + '</span>';
  }}
  html += '</div>';

  if (symKeys.length === 0) {{
    html = '<div style="padding:30px;text-align:center;color:var(--text-dim);">No trading activity in this date range.</div>';
  }}
  container.innerHTML = html;
}}

// Load gantt on portfolio page visit
ganttSetDefaultDates();
setTimeout(ganttLoad, 1000);

// ── P&L CALENDAR ──────────────────────────────────────────────
var calData = {{}};
var calTradeData = {{}};
var calYear = new Date().getFullYear();
var calMonth = new Date().getMonth(); // 0-indexed

async function calLoadData() {{
  // Load P&L snapshots
  try {{
    if (await isAlpacaConfigured()) {{
      var json = await apiGet('/api/alpaca/daily-pnl');
      calData = json.snapshots || {{}};
    }} else {{
      throw new Error('Alpaca API keys are not configured');
    }}
  }} catch (e) {{
    try {{
      var fallback = await apiGet('/api/broker/daily-pnl');
      calData = fallback.snapshots || {{}};
    }} catch (e2) {{
      calData = {{}};
    }}
  }}
  // Load trade journal for per-day trade counts
  try {{
    var journal = await apiGet('/api/trade-journal?limit=2000');
    var events = journal.events || [];
    calTradeData = {{}};
    events.forEach(function(ev) {{
      if (ev.event === 'order_submitted' || ev.event === 'position_closed') {{
        var ts = ev.timestamp || '';
        var dateKey = ts.substring(0, 10);
        if (!dateKey) return;
        if (!calTradeData[dateKey]) calTradeData[dateKey] = {{ count: 0, wins: 0 }};
        calTradeData[dateKey].count++;
        if (ev.event === 'position_closed') {{
          var plPct = Number(ev.unrealized_pl_pct || 0);
          if (plPct > 0) calTradeData[dateKey].wins++;
        }}
      }}
    }});
  }} catch (e3) {{
    calTradeData = {{}};
  }}
  calRender();
}}

function calExtractChange(snap, prevSnap) {{
  try {{
    if (snap && snap.day_pl !== undefined) {{
      return {{
        pnl: Number(snap.day_pl || 0),
        pct: Number(snap.day_pl_pct || 0),
        equity: Number(snap.equity || 0),
      }};
    }}
    var prevEquity = (prevSnap && prevSnap.equity !== undefined) ? Number(prevSnap.equity || 0) : Number((snap && snap.starting_balance) || 1000);
    var equity = Number((snap && snap.equity) || 0);
    var dayPnl = equity - prevEquity;
    var dayPct = prevEquity > 0 ? (dayPnl / prevEquity * 100) : 0;
    return {{ pnl: dayPnl, pct: dayPct, equity: equity }};
  }} catch (e) {{
    return {{ pnl: 0, pct: 0, equity: 0 }};
  }}
}}

function calPrev() {{
  calMonth--;
  if (calMonth < 0) {{ calMonth = 11; calYear--; }}
  calRender();
}}

function calNext() {{
  calMonth++;
  if (calMonth > 11) {{ calMonth = 0; calYear++; }}
  calRender();
}}

function calRender() {{
  var monthNames = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  document.getElementById('calMonthLabel').textContent = monthNames[calMonth] + ' ' + calYear;

  var grid = document.getElementById('calGrid');
  var headers = [];
  for (var h = 0; h < 7 && h < grid.children.length; h++) {{
    headers.push(grid.children[h]);
  }}
  grid.innerHTML = '';
  headers.forEach(function(hdr) {{ grid.appendChild(hdr); }});

  var firstDay = new Date(calYear, calMonth, 1).getDay();
  var daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  var today = new Date();
  var todayStr = today.getFullYear() + '-' + String(today.getMonth()+1).padStart(2,'0') + '-' + String(today.getDate()).padStart(2,'0');

  var sortedDates = Object.keys(calData).sort();
  var dailyChanges = {{}};
  for (var i = 0; i < sortedDates.length; i++) {{
    var d = sortedDates[i];
    var snap = calData[d];
    var prevSnap = i > 0 ? calData[sortedDates[i-1]] : null;
    dailyChanges[d] = calExtractChange(snap, prevSnap);
  }}

  for (var e = 0; e < firstDay; e++) {{
    var empty = document.createElement('div');
    empty.className = 'pnl-cal-cell empty';
    grid.appendChild(empty);
  }}

  var monthPnl = 0;
  var bestDay = null;
  var worstDay = null;
  var daysTracked = 0;
  var greenDays = 0;
  var redDays = 0;
  var firstEquity = null;
  var lastEquity = null;
  var totalTrades = 0;
  var winningTrades = 0;

  for (var day = 1; day <= daysInMonth; day++) {{
    var dateStr = calYear + '-' + String(calMonth+1).padStart(2,'0') + '-' + String(day).padStart(2,'0');
    var cell = document.createElement('div');
    cell.className = 'pnl-cal-cell';

    var dayLabel = document.createElement('div');
    dayLabel.className = 'cal-day';
    dayLabel.textContent = day;
    cell.appendChild(dayLabel);

    if (dateStr === todayStr) cell.classList.add('today');

    var change = dailyChanges[dateStr];
    if (change) {{
      daysTracked++;
      if (firstEquity === null) firstEquity = change.equity - change.pnl;
      lastEquity = change.equity;
      monthPnl += change.pnl;

      // Count trades for this day from journal data
      var dayTrades = (calTradeData && calTradeData[dateStr]) || {{}};
      var dayTradeCount = dayTrades.count || 0;
      var dayWins = dayTrades.wins || 0;
      totalTrades += dayTradeCount;
      winningTrades += dayWins;

      var pnlEl = document.createElement('div');
      pnlEl.className = 'cal-pnl-usd';
      var absPnl = Math.abs(change.pnl);
      if (absPnl >= 1000) {{
        pnlEl.textContent = (change.pnl >= 0 ? '+$' : '-$') + (absPnl/1000).toFixed(1) + 'K';
      }} else {{
        pnlEl.textContent = (change.pnl >= 0 ? '+$' : '-$') + absPnl.toFixed(2);
      }}
      cell.appendChild(pnlEl);

      var pctEl = document.createElement('div');
      pctEl.className = 'cal-pnl-pct';
      pctEl.textContent = (change.pct >= 0 ? '+' : '') + change.pct.toFixed(2) + '%';
      cell.appendChild(pctEl);

      if (dayTradeCount > 0) {{
        var tradeEl = document.createElement('div');
        tradeEl.className = 'cal-trades';
        tradeEl.textContent = dayTradeCount + ' trade' + (dayTradeCount !== 1 ? 's' : '');
        cell.appendChild(tradeEl);
      }}

      if (change.pnl > 0) {{ cell.classList.add('positive'); greenDays++; }}
      else if (change.pnl < 0) {{ cell.classList.add('negative'); redDays++; }}
      else cell.classList.add('zero');

      if (bestDay === null || change.pnl > bestDay.pnl) bestDay = {{ pnl: change.pnl, date: dateStr }};
      if (worstDay === null || change.pnl < worstDay.pnl) worstDay = {{ pnl: change.pnl, date: dateStr }};
    }}

    grid.appendChild(cell);
  }}

  // Stats bar (top of calendar)
  var statsBar = document.getElementById('calStatsBar');
  if (daysTracked > 0) {{
    statsBar.style.display = 'grid';
    var pnlColor = monthPnl >= 0 ? 'var(--lime)' : 'var(--red)';
    var monthPct = firstEquity > 0 ? (monthPnl / firstEquity * 100) : 0;

    var statPnl = document.getElementById('calStatPnl');
    var absMPnl = Math.abs(monthPnl);
    statPnl.textContent = (monthPnl >= 0 ? '+$' : '-$') + (absMPnl >= 1000 ? (absMPnl/1000).toFixed(2) + 'K' : absMPnl.toFixed(2));
    statPnl.style.color = pnlColor;

    var statPct = document.getElementById('calStatPct');
    statPct.textContent = (monthPct >= 0 ? '+' : '') + monthPct.toFixed(2) + '%';
    statPct.style.color = pnlColor;

    document.getElementById('calStatTrades').textContent = totalTrades;
    var winRate = totalTrades > 0 ? ((winningTrades / totalTrades) * 100).toFixed(1) + '%' : '—';
    document.getElementById('calStatWinRate').textContent = winRate;
    var greenEl = document.getElementById('calStatGreen');
    greenEl.textContent = greenDays;
    greenEl.style.color = 'var(--lime)';
    var redEl = document.getElementById('calStatRed');
    redEl.textContent = redDays;
    redEl.style.color = 'var(--red)';
  }} else {{
    statsBar.style.display = 'none';
  }}

  // Bottom summary
  var summaryEl = document.getElementById('calSummary');
  if (daysTracked > 0) {{
    summaryEl.style.display = 'flex';
    document.getElementById('calSumBest').textContent = bestDay ? ((bestDay.pnl >= 0 ? '+$' : '-$') + Math.abs(bestDay.pnl).toFixed(2)) : '—';
    document.getElementById('calSumBest').style.color = bestDay && bestDay.pnl >= 0 ? 'var(--lime)' : 'var(--red)';
    document.getElementById('calSumWorst').textContent = worstDay ? ((worstDay.pnl >= 0 ? '+$' : '-$') + Math.abs(worstDay.pnl).toFixed(2)) : '—';
    document.getElementById('calSumWorst').style.color = worstDay && worstDay.pnl >= 0 ? 'var(--lime)' : 'var(--red)';
    var avgDay = daysTracked > 0 ? monthPnl / daysTracked : 0;
    var avgEl = document.getElementById('calSumAvg');
    avgEl.textContent = (avgDay >= 0 ? '+$' : '-$') + Math.abs(avgDay).toFixed(2);
    avgEl.style.color = avgDay >= 0 ? 'var(--lime)' : 'var(--red)';
    document.getElementById('calSumDays').textContent = daysTracked;
    var notice = document.getElementById('calFirstDayNotice');
    if (notice) notice.style.display = 'none';
  }} else {{
    summaryEl.style.display = 'none';
    var notice = document.getElementById('calFirstDayNotice');
    if (!notice) {{
      notice = document.createElement('div');
      notice.id = 'calFirstDayNotice';
      notice.style.cssText = 'text-align:center;padding:16px;color:var(--text-dim);font-size:0.9em;margin-top:8px;background:rgba(0,212,255,0.05);border:1px solid var(--border);border-radius:8px;';
      notice.innerHTML = '📊 <strong>No daily P&L data yet.</strong> Alpaca needs at least one full trading day to generate history. Check back tomorrow!';
      var calSection = document.getElementById('pnlCalendarSection');
      if (calSection) calSection.appendChild(notice);
    }} else {{
      notice.style.display = 'block';
    }}
  }}
}}

// Load calendar data on page load
calLoadData();

// ── ALPACA LIVE PAGE ─────────────────────────────────────────
var alpLiveConnected = false;

function alpLiveSelectBroker(broker) {{
  // For now only alpaca is supported
  document.getElementById('brokerAlpacaBtn').style.background = 'rgba(0,212,255,0.2)';
  document.getElementById('brokerAlpacaBtn').style.borderColor = 'var(--cyan)';
}}

async function alpLiveCheckStatus() {{
  try {{
    var data = await apiGet('/api/alpaca/status');
    if (!data.configured) {{
      document.getElementById('alpLiveConnCard').style.display = 'none';
      document.getElementById('alpLiveNotConfigured').style.display = 'block';
      return false;
    }}
    document.getElementById('alpLiveConnCard').style.display = 'block';
    document.getElementById('alpLiveNotConfigured').style.display = 'none';
    return true;
  }} catch(e) {{
    document.getElementById('alpLiveConnMsg').textContent = 'Could not check Alpaca status: ' + e.message;
    return false;
  }}
}}

async function alpLiveConnect() {{
  var btn = document.getElementById('alpLiveConnBtn');
  var statusEl = document.getElementById('alpLiveConnStatus');
  var msgEl = document.getElementById('alpLiveConnMsg');
  btn.disabled = true;
  btn.textContent = '⏳ Connecting...';
  statusEl.textContent = '🟡 Connecting...';
  statusEl.style.color = 'var(--amber)';
  msgEl.textContent = 'Reaching out to Alpaca paper trading API...';
  try {{
    var data = await apiPost('/api/alpaca/connect', {{}});
    if (data.connected) {{
      alpLiveConnected = true;
      statusEl.textContent = '🟢 Connected';
      statusEl.style.color = 'var(--lime)';
      msgEl.textContent = 'Account ' + (data.account.account_number || '') + ' · ' + (data.account.status || '');
      btn.textContent = '✅ Connected';
      btn.style.borderColor = 'var(--lime)';
      btn.style.color = 'var(--lime)';
      document.getElementById('alpLiveAccountSection').style.display = 'block';
      document.getElementById('alpLiveNotConfigured').style.display = 'none';
      alpLiveUpdateAccount(data.account);
      alpLiveRefreshPositions();
      alpLiveRefreshOrders();
      alpLiveRefreshFeeAnalysis();
      alpAutoLoadStatus();
    }} else {{
      statusEl.textContent = '🔴 Failed';
      statusEl.style.color = 'var(--red)';
      msgEl.textContent = data.error || 'Connection failed';
      btn.textContent = '🔗 Retry';
      btn.disabled = false;
    }}
  }} catch(e) {{
    statusEl.textContent = '🔴 Error';
    statusEl.style.color = 'var(--red)';
    msgEl.textContent = e.message || 'Network error';
    btn.textContent = '🔗 Retry';
    btn.disabled = false;
  }}
}}

function alpLiveUpdateAccount(acct) {{
  _alpacaAccountCache = acct;
  _alpacaAccountFetchedAt = Date.now();
  var eqEl = document.getElementById('alpLiveEquity');
  eqEl.textContent = fmtUSD(acct.equity);
  autoSizeCardValue(eqEl);
  var cashEl = document.getElementById('alpLiveCash');
  cashEl.textContent = fmtUSD(acct.cash);
  autoSizeCardValue(cashEl);
  var pl = acct.total_pl || 0;
  var plEl = document.getElementById('alpLivePL');
  plEl.textContent = (pl >= 0 ? '+' : '') + fmtUSD(pl);
  plEl.style.color = pl >= 0 ? 'var(--lime)' : 'var(--red)';
  autoSizeCardValue(plEl);
  document.getElementById('alpLiveAccNum').textContent = acct.account_number || '—';
}}

var _alpLivePositionsInFlight = false;
var _alpFeeConfig = {{maker_bps:15, taker_bps:25, default_order_type:'taker'}};

function alpFeeEstimate(notional) {{
  var bps = (_alpFeeConfig.default_order_type || 'taker') === 'maker' ? Number(_alpFeeConfig.maker_bps || 15) : Number(_alpFeeConfig.taker_bps || 25);
  return Number(notional || 0) * bps / 10000;
}}

function alpLiveRenderOpenFeePreview(positions, riskMap) {{
  positions = positions || [];
  riskMap = riskMap || {{}};
  var emptyEl = document.getElementById('alpFeeOpenEmpty');
  var tableEl = document.getElementById('alpFeeOpenTable');
  var body = document.getElementById('alpFeeOpenBody');
  var openNetEl = document.getElementById('alpFeeOpenNet');
  if (!emptyEl || !tableEl || !body) return;
  if (!positions.length) {{
    emptyEl.style.display = 'block';
    tableEl.style.display = 'none';
    if (openNetEl) openNetEl.textContent = '—';
    return;
  }}
  emptyEl.style.display = 'none';
  tableEl.style.display = 'block';
  body.innerHTML = '';
  var totalNet = 0;
  var totalFees = 0;
  positions.forEach(function(p) {{
    var sym = p.symbol || '?';
    var r = riskMap[sym] || riskMap[String(sym).replace('/', '')] || {{}};
    // Always use Alpaca cost_basis — risk book entry_notional only tracks
    // the last fill, not accumulated position cost.
    var entryNotional = Number(p.cost_basis || 0);
    var markNotional = Number(p.market_value || 0);
    var entryPrice = Number(p.avg_entry_price || 0);
    var markPrice = Number(p.current_price || 0);
    var gross = markNotional - entryNotional;
    var fees = alpFeeEstimate(entryNotional) + alpFeeEstimate(markNotional);
    var net = gross - fees;
    totalNet += net;
    totalFees += fees;
    var grossColor = gross >= 0 ? 'var(--lime)' : 'var(--red)';
    var netColor = net >= 0 ? 'var(--lime)' : 'var(--red)';
    body.innerHTML += '<tr>' +
      '<td style="font-weight:700;color:var(--cyan);">' + sym + '<span class="muted-line">' + (r.strategy || 'manual/legacy') + '</span></td>' +
      '<td class="num">' + fmtUSD(entryNotional) + '<span class="muted-line">@ ' + (entryPrice ? fmtUSD(entryPrice) : '—') + '</span></td>' +
      '<td class="num">' + fmtUSD(markNotional) + '<span class="muted-line">@ ' + (markPrice ? fmtUSD(markPrice) : '—') + '</span></td>' +
      '<td class="num" style="color:' + grossColor + ';font-weight:700;">' + (gross >= 0 ? '+' : '') + fmtUSD(gross) + '</td>' +
      '<td class="num" style="color:var(--amber);">' + fmtUSD(fees) + '</td>' +
      '<td class="num" style="color:' + netColor + ';font-weight:800;">' + (net >= 0 ? '+' : '') + fmtUSD(net) + '</td>' +
      '</tr>';
  }});
  setCardText('alpFeeOpenNet', (totalNet >= 0 ? '+' : '') + fmtUSD(totalNet), totalNet >= 0 ? 'var(--lime)' : 'var(--red)');
}}

async function alpLiveRefreshPositions(opts) {{
  if (!alpLiveConnected) return;
  opts = opts || {{}};
  if (_alpLivePositionsInFlight) return;
  _alpLivePositionsInFlight = true;
  try {{
    var data = opts.snapshot || await getLivePositionsSnapshot();
    var riskData = await apiGet('/api/position-risk').catch(function() {{ return {{positions: {{}}}}; }});
    var riskMap = riskData.positions || {{}};
    function canonCryptoSymbol(sym) {{
      if (!sym) return sym;
      var s = String(sym).toUpperCase().replace(/\\s+/g, '');
      if (s.endsWith('/USDT')) return s.slice(0, -5) + '/USD';
      if (s.indexOf('/') < 0 && s.endsWith('USDT')) return s.slice(0, -4) + '/USD';
      if (s.indexOf('/') < 0 && s.endsWith('USD')) return s.slice(0, -3) + '/USD';
      return s;
    }}
    function fmtPrice(n) {{
      n = Number(n || 0);
      if (!isFinite(n) || n <= 0) return '—';
      if (n >= 1000) return '$' + n.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
      if (n >= 1) return '$' + n.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:4}});
      return '$' + n.toLocaleString(undefined, {{minimumFractionDigits:4, maximumFractionDigits:6}});
    }}
    function riskPrice(entry, pct, side) {{
      entry = Number(entry || 0);
      pct = Number(pct || 0);
      if (!entry || !pct) return null;
      return side === 'stop' ? entry * (1 - pct / 100) : entry * (1 + pct / 100);
    }}
    function trailPrice(high, pct) {{
      high = Number(high || 0);
      pct = Number(pct || 0);
      if (!high || !pct) return null;
      return high * (1 - pct / 100);
    }}
    function esc(txt) {{
      return String(txt === undefined || txt === null ? '' : txt)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }}
    function shortReason(txt) {{
      txt = String(txt || '').trim();
      return txt.length > 260 ? txt.slice(0, 260) + '...' : txt;
    }}
    var positions = data.positions || [];
    var emptyEl = document.getElementById('alpLivePositionsEmpty');
    var tableEl = document.getElementById('alpLivePositionsTable');
    var summEl = document.getElementById('alpLivePosSummary');
    if (positions.length === 0) {{
      emptyEl.style.display = 'block';
      tableEl.style.display = 'none';
      summEl.style.display = 'none';
      return;
    }}
    emptyEl.style.display = 'none';
    tableEl.style.display = 'block';
    summEl.style.display = 'block';
    var body = document.getElementById('alpLivePositionsBody');
    body.innerHTML = '';
    positions.forEach(function(p) {{
      var sym = canonCryptoSymbol(p.symbol);
      var rawSym = p.raw_symbol || (p.symbol ? String(p.symbol).replace('/', '') : '');
      var r = riskMap[sym] || riskMap[p.symbol] || riskMap[rawSym] || {{}};
      var plColor = p.unrealized_pl >= 0 ? 'var(--lime)' : 'var(--red)';
      var plSign = p.unrealized_pl >= 0 ? '+' : '';
      var entry = Number(r.entry_price || p.avg_entry_price || 0);
      var high = Number(r.high_water_price || p.current_price || entry || 0);
      var sl = riskPrice(entry, r.stop_loss_pct, 'stop');
      var tp = riskPrice(entry, r.take_profit_pct, 'take');
      var trail = trailPrice(high, r.trailing_stop_pct);
      var reason = shortReason(r.entry_reason || 'No bot entry reason stored for this position.');
      var strategy = r.strategy || 'manual/legacy';
      var regime = r.regime || 'unknown';
      var pnlPct = Number(p.unrealized_plpc || 0);
      body.innerHTML += '<tr>' +
        '<td style="font-weight:700;color:var(--cyan);">' + esc(sym) + '<span class="muted-line">Qty ' + Number(p.qty || 0).toFixed(4) + ' · Entry ' + fmtPrice(entry || p.avg_entry_price) + '</span></td>' +
        '<td class="price-cell num">' + fmtPrice(p.current_price) + '</td>' +
        '<td class="num">' + fmtUSD(p.market_value) + '</td>' +
        '<td class="num" style="color:' + plColor + ';font-weight:700;">' + plSign + fmtUSD(p.unrealized_pl) + '<span class="muted-line" style="color:' + plColor + ';">' + plSign + pnlPct.toFixed(2) + '%</span></td>' +
        '<td class="risk-cell"><span style="color:var(--red);">SL ' + fmtPrice(sl) + '</span><br><span style="color:var(--lime);">TP ' + fmtPrice(tp) + '</span><br><span style="color:var(--amber);">Trail ' + fmtPrice(trail) + '</span></td>' +
        '<td>' + esc(strategy) + '<span class="muted-line">conf ' + (r.confidence !== undefined && r.confidence !== null ? Number(r.confidence).toFixed(2) : '—') + '</span></td>' +
        '<td>' + esc(regime) + '</td>' +
        '</tr>' +
        '<tr class="reason-row"><td colspan="7"><strong>Why:</strong> ' + esc(reason) + '</td></tr>';
    }});
    alpLiveRenderOpenFeePreview(positions, riskMap);
    if (data.summary) {{
      var s = data.summary;
      var tColor = s.total_unrealized_pl >= 0 ? 'var(--lime)' : 'var(--red)';
      document.getElementById('alpLivePosTotal').innerHTML =
        '<span style="color:var(--text);">' + s.count + ' positions · ' + fmtUSD(s.total_market_value) + '</span>' +
        ' · <span style="color:' + tColor + ';">' + (s.total_unrealized_pl >= 0 ? '+' : '') + fmtUSD(s.total_unrealized_pl) + '</span>';
    }}
    var acct = !opts.skipAccount ? await getAlpacaAccountCached(0) : _alpacaAccountCache;
    if (acct) {{
      var m = liveAccountMetrics(acct, data);
      setCardText('alpLiveEquity', money(m.liveEquity));
      setCardText('alpLiveCash', money(m.cash));
      setCardText('alpLivePL', (m.dayPL >= 0 ? '+' : '') + money(m.dayPL), m.dayPL >= 0 ? 'var(--lime)' : 'var(--red)');
      var accEl = document.getElementById('alpLiveAccNum');
      if (accEl) accEl.textContent = acct.account_number || '—';
    }}
  }} catch(e) {{
    console.error('Positions refresh error:', e);
  }} finally {{
    _alpLivePositionsInFlight = false;
  }}
}}

async function alpLiveRefreshJournal() {{
  try {{
    var data = await apiGet('/api/trade-journal?limit=60');
    var events = data.events || [];
    var body = document.getElementById('alpLiveJournalBody');
    if (!body) return;
    if (events.length === 0) {{
      body.innerHTML = 'No real paper-trading journal events yet.';
      return;
    }}
    body.innerHTML = events.slice(0, 60).map(function(e) {{
      var ts = (e.timestamp || '').replace('T', ' ').slice(0, 19);
      var sym = e.symbol || '—';
      var event = e.event || 'event';
      var reason = e.reason || e.entry_reason || '';
      var conf = e.confidence !== undefined && e.confidence !== null ? ' · conf ' + Number(e.confidence).toFixed(2) : '';
      return '<div style="padding:8px 0;border-bottom:1px solid var(--border);">' +
        '<span style="color:var(--cyan);font-weight:600;">' + sym + '</span> ' +
        '<span style="color:var(--text);">' + event + '</span>' +
        '<span style="color:var(--text-dim);"> · ' + ts + conf + '</span><br>' +
        '<span>' + reason + '</span>' +
        '</div>';
    }}).join('');
    await alpLiveRefreshGuardReadout(events);
  }} catch(e) {{}}
}}

async function alpLiveRefreshGuardReadout(events) {{
  try {{
    var learning = await apiGet('/api/learning/live-status').catch(function() {{ return {{blocked_pairs: []}}; }});
    var blockedPairs = learning.blocked_pairs || [];
    var guard = summarizeGuardData(events || []);
    setCardText('alpGuardCooldown', String(guard.cooldownSymbols.length), guard.cooldownSymbols.length ? 'var(--amber)' : null);
    setCardText('alpGuardTradeCaps', String(guard.cappedSymbols.length), guard.cappedSymbols.length ? 'var(--amber)' : null);
    setCardText('alpGuardLossExits', String(guard.lossExits24h), guard.lossExits24h ? 'var(--red)' : null);
    setCardText('alpGuardBlocks', String(blockedPairs.length), blockedPairs.length ? 'var(--red)' : null);

    var exitEmpty = document.getElementById('alpGuardExitEmpty');
    var exitTable = document.getElementById('alpGuardExitTable');
    var exitBody = document.getElementById('alpGuardExitBody');
    if (exitEmpty && exitTable && exitBody) {{
      if (!guard.exitRows.length) {{
        exitEmpty.style.display = 'block';
        exitTable.style.display = 'none';
      }} else {{
        exitEmpty.style.display = 'none';
        exitTable.style.display = 'block';
        exitBody.innerHTML = guard.exitRows.map(function(row) {{
          return '<tr><td>' + escHtml(row.reason) + '</td><td class="num">' + Number(row.count || 0) + '</td></tr>';
        }}).join('');
      }}
    }}

    var blockEmpty = document.getElementById('alpGuardBlockEmpty');
    var blockTable = document.getElementById('alpGuardBlockTable');
    var blockBody = document.getElementById('alpGuardBlockBody');
    if (blockEmpty && blockTable && blockBody) {{
      if (!blockedPairs.length) {{
        blockEmpty.style.display = 'block';
        blockTable.style.display = 'none';
      }} else {{
        blockEmpty.style.display = 'none';
        blockTable.style.display = 'block';
        blockBody.innerHTML = blockedPairs.slice(0, 8).map(function(row) {{
          return '<tr>' +
            '<td><strong>' + escHtml(row.strategy || 'unknown') + '</strong></td>' +
            '<td>' + escHtml(row.regime || 'unknown') + '</td>' +
            '<td class="num">' + Number(row.trades || 0) + '</td>' +
            '<td class="num">' + pct(row.win_rate || 0) + '</td>' +
            '</tr>';
        }}).join('');
      }}
    }}
  }} catch(e) {{}}
}}

async function alpLiveRefreshFeeAnalysis() {{
  try {{
    var data = await apiGet('/api/alpaca/fee-analysis?live=0');
    if (data.fee_config) _alpFeeConfig = data.fee_config;
    var s = data.summary || {{}};
    var net = Number(s.realized_net_pl || 0);
    var fees = Number(s.realized_estimated_fees || 0);
    setCardText('alpFeeRealizedNet', (net >= 0 ? '+' : '') + fmtUSD(net), net >= 0 ? 'var(--lime)' : 'var(--red)');
    setCardText('alpFeeRealizedFees', fmtUSD(fees), 'var(--amber)');
    setCardText('alpFeeWinRate', s.net_win_rate === null || s.net_win_rate === undefined ? '—' : Number(s.net_win_rate).toFixed(1) + '%');
    var ledgerEl = document.getElementById('alpFeeLedgerPath');
    if (ledgerEl && data.trade_ledger_csv) ledgerEl.textContent = 'CSV ledger: ' + data.trade_ledger_csv;

    var closed = data.closed_trades || [];
    var emptyEl = document.getElementById('alpFeeClosedEmpty');
    var tableEl = document.getElementById('alpFeeClosedTable');
    var body = document.getElementById('alpFeeClosedBody');
    if (!emptyEl || !tableEl || !body) return;
    if (!closed.length) {{
      emptyEl.style.display = 'block';
      tableEl.style.display = 'none';
      body.innerHTML = '';
      return;
    }}
    emptyEl.style.display = 'none';
    tableEl.style.display = 'block';
    body.innerHTML = '';
    closed.slice(0, 40).forEach(function(t) {{
      var gross = Number(t.gross_pl || 0);
      var netPl = Number(t.net_pl || 0);
      var grossColor = gross >= 0 ? 'var(--lime)' : 'var(--red)';
      var netColor = netPl >= 0 ? 'var(--lime)' : 'var(--red)';
      var ts = t.timestamp ? new Date(t.timestamp).toLocaleString() : '—';
      body.innerHTML += '<tr>' +
        '<td style="font-size:0.82em;">' + ts + '</td>' +
        '<td style="font-weight:700;color:var(--cyan);">' + (t.symbol || '—') + '<span class="muted-line">' + (t.strategy || 'unknown') + '</span></td>' +
        '<td class="num">' + fmtUSD(t.entry_notional || 0) + '<span class="muted-line">@ ' + (t.entry_price ? fmtUSD(t.entry_price) : '—') + '</span></td>' +
        '<td class="num">' + fmtUSD(t.exit_notional || 0) + '<span class="muted-line">@ ' + (t.exit_price ? fmtUSD(t.exit_price) : '—') + '</span></td>' +
        '<td class="num" style="color:' + grossColor + ';font-weight:700;">' + (gross >= 0 ? '+' : '') + fmtUSD(gross) + '</td>' +
        '<td class="num" style="color:var(--amber);">' + fmtUSD(t.total_fees || 0) + '</td>' +
        '<td class="num" style="color:' + netColor + ';font-weight:800;">' + (netPl >= 0 ? '+' : '') + fmtUSD(netPl) + '<span class="muted-line" style="color:' + netColor + ';">' + (Number(t.net_pl_pct || 0) >= 0 ? '+' : '') + Number(t.net_pl_pct || 0).toFixed(2) + '%</span></td>' +
        '<td style="max-width:280px;">' + (t.exit_reason || '—') + '</td>' +
        '</tr>';
    }});
  }} catch(e) {{
    console.error('Fee analysis refresh error:', e);
  }}
}}

async function alpLiveRefreshOrders() {{
  if (!alpLiveConnected) return;
  try {{
    var data = await apiGet('/api/alpaca/orders?limit=20');
    var orders = data.orders || [];
    var emptyEl = document.getElementById('alpLiveOrdersEmpty');
    var tableEl = document.getElementById('alpLiveOrdersTable');
    if (orders.length === 0) {{
      emptyEl.style.display = 'block';
      tableEl.style.display = 'none';
      return;
    }}
    emptyEl.style.display = 'none';
    tableEl.style.display = 'block';
    var body = document.getElementById('alpLiveOrdersBody');
    body.innerHTML = '';
    orders.forEach(function(o) {{
      var cleanSide = (o.side || '').toLowerCase().split('.').pop();
      var cleanStatus = (o.status || '').split('.').pop();
      var sideColor = cleanSide === 'buy' ? 'var(--lime)' : 'var(--red)';
      var time = o.submitted_at ? new Date(o.submitted_at).toLocaleString() : '—';
      var fillPrice = o.filled_avg_price ? fmtUSD(o.filled_avg_price) : '—';
      var amount = o.notional ? fmtUSD(o.notional) : (o.qty ? o.qty + ' units' : '—');
      var statusColor = cleanStatus === 'filled' ? 'var(--lime)' : (cleanStatus === 'canceled' || cleanStatus === 'cancelled' ? 'var(--red)' : 'var(--amber)');
      body.innerHTML += '<tr>' +
        '<td style="font-size:0.85em;">' + time + '</td>' +
        '<td style="font-weight:600;color:var(--cyan);">' + o.symbol + '</td>' +
        '<td style="color:' + sideColor + ';font-weight:600;">' + cleanSide.toUpperCase() + '</td>' +
        '<td class="num">' + amount + '</td>' +
        '<td class="num">' + fillPrice + '</td>' +
        '<td style="color:' + statusColor + ';">' + cleanStatus.toUpperCase() + '</td>' +
        '</tr>';
    }});
  }} catch(e) {{
    console.error('Orders refresh error:', e);
  }}
}}

async function alpLiveTrade(side) {{
  if (!alpLiveConnected) {{ alert('Connect to Alpaca first.'); return; }}
  var symbol = document.getElementById('alpLiveTradeSymbol').value.trim();
  var amount = parseFloat(document.getElementById('alpLiveTradeAmount').value);
  if (!symbol) {{ alert('Enter a symbol (e.g. BTC/USD).'); return; }}
  if (!amount || amount < 1) {{ alert('Enter an amount of at least $1.'); return; }}
  var resultEl = document.getElementById('alpLiveTradeResult');
  resultEl.style.display = 'block';
  resultEl.style.background = 'rgba(255,255,255,0.05)';
  resultEl.style.color = 'var(--text-dim)';
  resultEl.textContent = '⏳ Placing ' + side + ' order for ' + symbol + '...';
  try {{
    var data = await apiPost('/api/alpaca/execute', {{
      confirm: true, symbol: symbol, notional: amount, side: side
    }});
    if (data.error) {{
      resultEl.style.background = 'rgba(255,69,58,0.1)';
      resultEl.style.color = 'var(--red)';
      resultEl.textContent = '❌ ' + data.error;
    }} else {{
      resultEl.style.background = 'rgba(57,255,20,0.1)';
      resultEl.style.color = 'var(--lime)';
      resultEl.textContent = '✅ ' + (data.side||side).toUpperCase() + ' ' + data.symbol + ' — ' +
        (data.status || 'submitted') + (data.filled_avg_price ? ' at ' + fmtUSD(data.filled_avg_price) : '');
      setTimeout(function() {{ alpLiveRefreshPositions(); alpLiveRefreshOrders(); alpLiveRefreshFeeAnalysis(); }}, 2000);
    }}
  }} catch(e) {{
    resultEl.style.background = 'rgba(255,69,58,0.1)';
    resultEl.style.color = 'var(--red)';
    resultEl.textContent = '❌ ' + e.message;
  }}
}}

async function alpLiveClosePos(symbol) {{
  if (!confirm('Close position in ' + symbol + '?')) return;
  try {{
    var data = await apiPost('/api/alpaca/close-position', {{ confirm: true, symbol: symbol }});
    if (data.error) {{ alert('Error: ' + data.error); }}
    else {{ setTimeout(function() {{ alpLiveRefreshPositions(); alpLiveRefreshOrders(); alpLiveRefreshFeeAnalysis(); }}, 2000); }}
  }} catch(e) {{ alert('Error: ' + e.message); }}
}}

async function alpLiveCloseAll() {{
  if (!confirm('Close ALL open Alpaca positions?')) return;
  try {{
    var data = await apiPost('/api/alpaca/close-all', {{ confirm: true }});
    setTimeout(function() {{ alpLiveRefreshPositions(); alpLiveRefreshOrders(); alpLiveRefreshFeeAnalysis(); }}, 2000);
  }} catch(e) {{ alert('Error: ' + e.message); }}
}}

// Check Alpaca status on page load
alpLiveCheckStatus();

// ── ALPACA AUTO-TRADE CONTROLS ───────────────────────────────
var alpAutoEnabled = false;

async function alpAutoLoadStatus() {{
  try {{
    var data = await apiGet('/api/alpaca/auto/status');
    alpAutoEnabled = data.enabled;
    alpAutoUpdateUI(data);
  }} catch(e) {{}}
}}

function alpAutoUpdateUI(data) {{
  var badge = document.getElementById('alpAutoStatusBadge');
  var btn = document.getElementById('alpAutoToggleBtn');
  if (data.enabled) {{
    if (badge) {{
      badge.textContent = 'ON';
      badge.className = 'status-pill ok';
    }}
    if (btn) {{
      btn.textContent = 'Disable Auto-Trade';
      btn.style.borderColor = 'var(--red)';
      btn.style.color = 'var(--red)';
    }}
  }} else {{
    if (badge) {{
      badge.textContent = 'OFF';
      badge.className = 'status-pill warn';
    }}
    if (btn) {{
      btn.textContent = 'Enable Auto-Trade';
      btn.style.borderColor = 'var(--lime)';
      btn.style.color = 'var(--lime)';
    }}
  }}
  var statusEl = document.getElementById('alpAutoRunStatus');
  if (data.thread_alive) {{
    statusEl.textContent = data.enabled ? '🟢 Running' : '🟡 Idle (disabled)';
    statusEl.style.color = data.enabled ? 'var(--lime)' : 'var(--amber)';
  }} else {{
    statusEl.textContent = '⚪ Stopped';
    statusEl.style.color = 'var(--gray)';
  }}
  document.getElementById('alpAutoInterval').textContent = (data.interval_min || 15) + ' min';
  if (data.last_run) {{
    document.getElementById('alpAutoLastRun').textContent = new Date(data.last_run).toLocaleString();
  }}
  if (data.next_run && data.enabled) {{
    document.getElementById('alpAutoNextRun').textContent = new Date(data.next_run).toLocaleString();
  }} else {{
    document.getElementById('alpAutoNextRun').textContent = data.enabled ? 'Soon' : '—';
  }}
  // Render logs
  var runs = data.recent_runs || [];
  var logsEl = document.getElementById('alpAutoLogs');
  var emptyEl = document.getElementById('alpAutoLogsEmpty');
  if (runs.length === 0) {{
    logsEl.style.display = 'none';
    emptyEl.style.display = 'block';
  }} else {{
    emptyEl.style.display = 'none';
    logsEl.style.display = 'block';
    var html = '';
    runs.slice().reverse().forEach(function(r) {{
      var ts = r.timestamp ? new Date(r.timestamp).toLocaleTimeString() : '?';
      var statusColor = r.status === 'ok' ? 'var(--lime)' : (r.status === 'error' ? 'var(--red)' : 'var(--amber)');
      var s2 = (r.steps && r.steps.trade && r.steps.trade.summary) || {{}};
      var detail = '';
      if (s2.buys !== undefined) {{
        detail = ' · ' + (s2.buys||0) + ' buys, ' + (s2.sells||0) + ' sells, ' + (s2.closes||0) + ' closes';
      }}
      if (s2.equity_after || (r.steps && r.steps.trade && r.steps.trade.equity_after)) {{
        var eq = s2.equity_after || r.steps.trade.equity_after;
        detail += ' · equity $' + Number(eq).toFixed(2);
      }}
      html += '<div style="padding:6px 0;border-bottom:1px solid var(--border);">' +
        '<span style="color:var(--text-dim);">' + ts + '</span> ' +
        '<span style="color:' + statusColor + ';font-weight:600;">' + (r.status||'?').toUpperCase() + '</span>' +
        detail +
        (r.error ? ' <span style="color:var(--red);font-size:0.85em;">' + r.error.substring(0,80) + '</span>' : '') +
        '</div>';
    }});
    logsEl.innerHTML = html;
  }}
}}

async function alpAutoToggle() {{
  var newState = !alpAutoEnabled;
  try {{
    var data = await apiPost('/api/alpaca/auto/toggle', {{ enabled: newState }});
    alpAutoEnabled = data.enabled;
    alpAutoUpdateUI(data.status || data);
  }} catch(e) {{ alert('Error: ' + e.message); }}
}}

async function alpAutoOneClick() {{
  var btn = document.getElementById('alpAutoRunBtn');
  var area = document.getElementById('alpAutoProgressArea');
  var steps = document.getElementById('alpAutoProgressSteps');
  var result = document.getElementById('alpAutoProgressResult');
  area.style.display = 'block';
  result.innerHTML = '';
  btn.disabled = true;
  btn.textContent = '⏳ Running...';
  btn.style.opacity = '0.6';

  function step(num, text, status) {{
    var id = 'alpStep' + num;
    var el = document.getElementById(id);
    var icon = status === 'done' ? '✅' : (status === 'error' ? '❌' : (status === 'running' ? '⏳' : '⬜'));
    var color = status === 'done' ? 'var(--lime)' : (status === 'error' ? 'var(--red)' : (status === 'running' ? 'var(--amber)' : 'var(--text-dim)'));
    if (!el) {{
      el = document.createElement('div');
      el.id = id;
      el.style.cssText = 'padding:6px 0;border-bottom:1px solid var(--border);';
      steps.appendChild(el);
    }}
    el.innerHTML = '<span style="margin-right:8px;">' + icon + '</span><span style="color:' + color + ';">' + text + '</span>';
  }}

  steps.innerHTML = '';

  // Step 1: Preview (dry run)
  step(1, 'Previewing rebalance...', 'running');
  step(2, 'Execute trades', 'pending');
  step(3, 'Refresh dashboard', 'pending');

  var previewData = null;
  try {{
    previewData = await apiGet('/api/alpaca/auto/preview');
    if (previewData.error) {{
      step(1, 'Preview failed: ' + previewData.error, 'error');
      btn.disabled = false; btn.textContent = '🚀 Run Now'; btn.style.opacity = '';
      return;
    }}
    var s = previewData.summary || {{}};
    var orderCount = (s.buys||0) + (s.sells||0) + (s.closes||0);
    step(1, 'Preview: ' + (s.buys||0) + ' buys, ' + (s.sells||0) + ' sells, ' + (s.closes||0) + ' closes, ' + (s.skipped||0) + ' skipped', 'done');

    // Show preview details
    var orders = previewData.orders || [];
    if (orders.length > 0) {{
      var html = '<table style="width:100%;font-size:0.85em;margin-top:8px;"><tr style="color:var(--text-dim);"><th style="text-align:left;">Symbol</th><th>Side</th><th>Amount</th><th>Bot</th></tr>';
      orders.forEach(function(o) {{
        var cSide = (o.side || '').toLowerCase().split('.').pop();
        var sColor = cSide === 'buy' ? 'var(--lime)' : 'var(--red)';
        html += '<tr><td style="color:var(--cyan);font-weight:600;">' + (o.symbol||'?') + '</td>' +
          '<td style="color:' + sColor + ';">' + cSide.toUpperCase() + '</td>' +
          '<td>' + fmtUSD(o.notional||0) + '</td>' +
          '<td style="color:var(--text-dim);">' + (o.bot||'—') + '</td></tr>';
      }});
      html += '</table>';
      result.innerHTML = html;
    }}

    if (orderCount === 0) {{
      step(2, 'No trades needed — portfolio is balanced', 'done');
      step(3, 'Done', 'done');
      btn.disabled = false; btn.textContent = '🚀 Run Now'; btn.style.opacity = '';
      return;
    }}
  }} catch(e) {{
    step(1, 'Preview error: ' + e.message, 'error');
    btn.disabled = false; btn.textContent = '🚀 Run Now'; btn.style.opacity = '';
    return;
  }}

  // Step 2: Execute
  step(2, 'Executing trades...', 'running');
  try {{
    var execData = await apiPost('/api/alpaca/auto/execute', {{ confirm: true }});
    if (execData.error) {{
      step(2, 'Execute failed: ' + execData.error, 'error');
      btn.disabled = false; btn.textContent = '🚀 Run Now'; btn.style.opacity = '';
      return;
    }}
    var es = execData.summary || {{}};
    step(2, 'Executed: ' + (es.buys||0) + ' buys, ' + (es.sells||0) + ' sells, ' + (es.closes||0) + ' closes — ' + fmtUSD(es.total_capital_deployed_usd||0) + ' deployed', 'done');
  }} catch(e) {{
    step(2, 'Execute error: ' + e.message, 'error');
    btn.disabled = false; btn.textContent = '🚀 Run Now'; btn.style.opacity = '';
    return;
  }}

  // Step 3: Refresh everything
  step(3, 'Refreshing positions & orders...', 'running');
  try {{
    await new Promise(function(r) {{ setTimeout(r, 2000); }});
    await alpLiveRefreshPositions();
    await alpLiveRefreshOrders();
    await alpLiveRefreshFeeAnalysis();
    await alpAutoLoadStatus();
    await loadOverviewAccount();
    step(3, 'Dashboard refreshed', 'done');
  }} catch(e) {{
    step(3, 'Refresh done (some data may still update)', 'done');
  }}

  btn.disabled = false;
  btn.textContent = '🚀 Run Now';
  btn.style.opacity = '';
}}

// Load Alpaca auto-trade status on page load
alpAutoLoadStatus();

</script>"""

    # ── MOCK DATA ────────────────────────────────────────────────────────
    def _mock_data(self):
        """Generate realistic mock data for preview."""
        names = ["BTC Scalper Alpha","ETH Momentum Wave","SOL Trend Rider","BNB Mean Reverter",
                 "ADA Breakout Hunter","XRP Grid Master","DOT Swing Trader","AVAX Scalper Pro",
                 "LINK Trend Surfer","MATIC Momentum Beta","DOGE Breakout Blitz","UNI Mean Reverter Pro",
                 "ATOM Swing Elite","FTM Grid Optimizer","NEAR Scalper Gamma","ALGO Trend Catcher",
                 "APE Momentum Surge","CRV Breakout Seeker"]
        pairs = ["BTCUSD","ETHUSD","SOLUSD","BNBUSD","ADAUSD","XRPUSD","DOTUSD","AVAXUSD",
                 "LINKUSD","MATICUSD","DOGEUSD","UNIUSD","ATOMUSD","FTMUSD","NEARUSD","ALGOUSD","APEUSD","CRVUSD"]
        statuses = ["active","active","active","paused","active","active","active","paused","active",
                    "active","active","active","paused","active","active","active","active","active"]
        verdicts = ["HOLD","PAUSE","HOLD","PAUSE","HOLD","HOLD","HOLD","HOLD","HOLD",
                    "HOLD","PAUSE","HOLD","REACTIVATE","HOLD","HOLD","INSUFFICIENT_DATA","HOLD","HOLD"]
        wr = [68,52,71,48,61,75,66,45,72,58,51,69,77,64,70,49,73,60]
        pf = [1.45,0.92,1.68,0.85,1.22,1.92,1.54,0.78,1.85,1.31,0.88,1.71,2.04,1.48,1.76,0.81,1.93,1.39]
        sr = [0.68,0.22,0.95,0.15,0.48,1.12,0.72,0.18,1.08,0.55,0.28,0.88,1.35,0.78,0.98,0.08,1.18,0.65]
        md = [-12.5,-28.3,-8.2,-35.1,-18.9,-5.3,-14.2,-32.7,-7.1,-16.4,-26.8,-9.5,-4.2,-13.1,-6.8,-42.5,-3.9,-11.2]
        pnl = [2450,-380,1200,-520,890,3200,1100,-150,2800,1540,-90,2100,4300,1820,2650,-200,3100,1480]
        adapt = [72,35,78,28,55,81,68,38,84,62,42,75,88,71,79,15,85,65]
        trades = [145,128,156,98,112,167,134,86,178,121,95,143,189,127,151,104,169,116]

        bots_data = [{"name": names[i], "pair": pairs[i], "status": statuses[i]} for i in range(18)]

        evaluations = {}
        for i in range(18):
            evaluations[names[i]] = {
                "verdict": verdicts[i], "base_verdict": verdicts[i],
                "win_rate": wr[i], "profit_factor": pf[i], "sharpe_ratio": sr[i],
                "max_drawdown": md[i], "net_profit": pnl[i], "total_trades": trades[i],
                "adaptation_score": adapt[i], "adaptation_label": "NEUTRAL",
                "pair": pairs[i], "strategy_type": "mock", "bot_status": statuses[i],
                "reasons": ["Mock data for preview"],
            }

        regime_info = {"regime": "trending_up", "confidence": 72,
                       "details": {"volatility": 18, "trend_direction": 0.65, "autocorrelation": 0.42, "vol_ratio": 1.15}}
        learning = {n: {"adaptation_score": adapt[i]} for i, n in enumerate(names)}
        summary = {"PAUSE": 3, "HOLD": 13, "REACTIVATE": 1, "INSUFFICIENT_DATA": 1}

        portfolio = {
            "allocations": [{"bot_name": names[i], "pair": pairs[i], "allocation_usd": 1000/12,
                            "allocation_pct": 100/12, "score": adapt[i], "expected_monthly_return": pnl[i]*0.01,
                            "reasoning": f"Score {adapt[i]}"} for i in range(12) if verdicts[i] not in ("PAUSE","INSUFFICIENT_DATA")],
            "excluded": [{"bot_name": names[i], "reason": "Paused"} for i in range(18) if verdicts[i] == "PAUSE"],
            "summary": {"total_capital": 1000, "allocated": 1000, "diversification_score": 78,
                        "expected_monthly_return_usd": 42.5, "expected_monthly_return_pct": 4.25, "num_strategies": 12}
        }

        return bots_data, evaluations, regime_info, learning, summary, portfolio
