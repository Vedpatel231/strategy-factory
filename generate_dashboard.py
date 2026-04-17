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
            ("alpaca", "Paper Trading", "💵"),
            ("alpaca-live", "Alpaca", "🔗"),
            ("quantum", "Strategy Scorecard", "⚛️"),
            ("bots", "Bot Status", "🤖"),
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

        # Normalize learning_stats
        if isinstance(learning_stats, dict) and "calibration" in learning_stats and len(learning_stats) <= 2:
            cal = learning_stats.get("calibration", {})
            new_ls = {}
            for name, ev in evaluations.items():
                new_ls[name] = {
                    "adaptation_score": ev.get("adaptation_score", 50),
                    "adaptation_label": ev.get("adaptation_label", "NEUTRAL"),
                }
            new_ls["_calibration"] = cal
            learning_stats = new_ls

        ts = _now_est_label()
        parts = [self._head(ts)]
        parts.append(self._sidebar())
        parts.append('<div class="main-content">')
        parts.append(self._page_overview(bots_data, evaluations, regime_info, execution_summary, ts))
        parts.append(self._page_portfolio(evaluations, portfolio))
        parts.append(self._page_alpaca())
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
}}
html,body{{height:100%;}}
body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);line-height:1.6;overflow-x:hidden;display:flex;min-height:100vh;}}

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
.main-content{{margin-left:260px;flex:1;padding:32px 40px;min-height:100vh;}}
.page{{display:none;animation:fadeIn 0.4s ease;}}
.page.active{{display:block;}}
@keyframes fadeIn{{from{{opacity:0;transform:translateY(10px);}}to{{opacity:1;transform:translateY(0);}}}}
@keyframes pulse{{0%,100%{{opacity:1;}}50%{{opacity:0.5;}}}}

.page-title{{font-size:2.2em;font-weight:700;margin-bottom:28px;display:flex;align-items:center;gap:14px;color:var(--text);}}
.page-title .accent{{color:var(--cyan);}}

/* TOOLTIP */
.tip{{position:relative;cursor:help;border-bottom:1px dotted var(--text-dim);}}
.tip:hover::after{{
  content:attr(data-tip);position:absolute;bottom:100%;left:50%;transform:translateX(-50%);
  background:#0d1130;color:var(--cyan);padding:8px 12px;border-radius:6px;font-size:0.75em;
  white-space:nowrap;border:1px solid var(--border);z-index:1000;margin-bottom:8px;font-weight:500;
}}

/* CARDS */
.cards-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-bottom:28px;}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;transition:all 0.3s cubic-bezier(0.4,0,0.2,1);position:relative;overflow:hidden;}}
.card:hover{{transform:translateY(-4px);border-color:var(--cyan);box-shadow:0 8px 24px rgba(0,212,255,0.1);}}
.card-label{{font-size:0.75em;color:var(--text-dim);text-transform:uppercase;letter-spacing:1.2px;margin-bottom:10px;font-weight:600;}}
.card-value{{font-size:2.2em;font-weight:700;font-family:'Courier New',monospace;color:var(--cyan);letter-spacing:-1px;}}
.card-sub{{font-size:0.85em;color:var(--text-dim);margin-top:8px;}}

/* TABLE */
.data-table{{width:100%;border-collapse:collapse;font-size:0.9em;}}
.data-table th{{background:#151a3a;color:var(--cyan);padding:14px 16px;text-align:left;font-weight:600;border-bottom:2px solid var(--cyan);cursor:pointer;user-select:none;white-space:nowrap;}}
.data-table th:hover{{color:var(--lime);background:#1a2250;}}
.data-table td{{padding:12px 16px;border-bottom:1px solid var(--border);}}
.data-table tr:hover td{{background:rgba(0,212,255,0.04);}}
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
.bot-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:22px;transition:all 0.3s;}}
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
.chart-box{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;}}
.chart-box h3{{font-size:1em;color:var(--text);margin-bottom:14px;font-weight:600;}}
.chart-box canvas{{width:100%!important;height:280px!important;max-height:320px;}}
.chart-fallback{{display:flex;flex-direction:column;gap:10px;padding:4px 0;min-height:220px;justify-content:center;}}
.chart-fallback-note{{color:var(--text-dim);font-size:0.82em;margin-bottom:6px;}}
.chart-fallback-row{{display:flex;justify-content:space-between;gap:16px;padding:10px 12px;background:rgba(0,212,255,0.05);border:1px solid rgba(45,53,97,0.8);border-radius:10px;font-size:0.88em;}}
.chart-fallback-row strong{{color:var(--text);font-weight:600;}}
.chart-fallback-row span{{color:var(--cyan);font-family:'Courier New',monospace;}}
@media(max-width:1400px){{.chart-grid{{grid-template-columns:1fr;}}}}

/* PORTFOLIO */
.portfolio-hero{{background:linear-gradient(135deg,rgba(0,212,255,0.1) 0%,rgba(57,255,20,0.1) 100%);border:1px solid var(--border);border-radius:14px;padding:40px;margin-bottom:32px;text-align:center;}}
.portfolio-hero-value{{font-size:3.5em;font-weight:700;color:var(--cyan);font-family:'Courier New',monospace;margin:16px 0;}}
.portfolio-hero-subtitle{{font-size:1.1em;color:var(--text-dim);}}
.portfolio-grid{{display:grid;grid-template-columns:2fr 1fr;gap:24px;margin-bottom:32px;}}
.portfolio-excluded{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;margin-bottom:28px;}}
.portfolio-excluded h3{{color:var(--amber);margin-bottom:16px;}}
.excluded-item{{padding:12px;background:rgba(255,183,0,0.05);border-left:3px solid var(--amber);border-radius:6px;margin-bottom:10px;font-size:0.9em;}}
.portfolio-summary{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:24px;margin-bottom:28px;}}
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
.adapt-card{{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:20px;text-align:center;}}
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
.pnl-calendar-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;}}
.pnl-calendar-header h3{{font-size:1.1em;color:var(--text);font-weight:600;}}
.pnl-calendar-nav{{display:flex;align-items:center;gap:12px;}}
.pnl-calendar-nav button{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:6px 14px;cursor:pointer;color:var(--text-dim);font-size:0.9em;font-weight:500;transition:all 0.2s;}}
.pnl-calendar-nav button:hover{{border-color:var(--cyan);color:var(--cyan);}}
.pnl-calendar-nav .cal-month-label{{font-size:1em;font-weight:600;color:var(--cyan);min-width:140px;text-align:center;font-family:'Courier New',monospace;}}
.pnl-calendar-grid{{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;}}
.pnl-cal-dayheader{{text-align:center;font-size:0.7em;color:var(--text-dim);text-transform:uppercase;letter-spacing:1px;padding:8px 0;font-weight:600;}}
.pnl-cal-cell{{background:rgba(13,17,48,0.5);border:1px solid rgba(45,53,97,0.4);border-radius:8px;padding:8px 6px;min-height:68px;text-align:center;transition:all 0.2s;position:relative;}}
.pnl-cal-cell:hover{{border-color:var(--cyan);background:rgba(0,212,255,0.04);}}
.pnl-cal-cell.empty{{background:transparent;border-color:transparent;min-height:0;}}
.pnl-cal-cell .cal-day{{font-size:0.75em;color:var(--text-dim);margin-bottom:4px;font-weight:500;}}
.pnl-cal-cell .cal-pnl-usd{{font-size:0.85em;font-weight:700;font-family:'Courier New',monospace;}}
.pnl-cal-cell .cal-pnl-pct{{font-size:0.7em;font-family:'Courier New',monospace;margin-top:2px;}}
.pnl-cal-cell.positive .cal-pnl-usd{{color:var(--lime);}}
.pnl-cal-cell.positive .cal-pnl-pct{{color:var(--lime);}}
.pnl-cal-cell.negative .cal-pnl-usd{{color:var(--red);}}
.pnl-cal-cell.negative .cal-pnl-pct{{color:var(--red);}}
.pnl-cal-cell.zero .cal-pnl-usd{{color:var(--text-dim);}}
.pnl-cal-cell.zero .cal-pnl-pct{{color:var(--text-dim);}}
.pnl-cal-cell.today{{border-color:var(--cyan);box-shadow:0 0 8px rgba(0,212,255,0.2);}}
.pnl-cal-summary{{display:flex;gap:24px;margin-top:16px;padding:14px 20px;background:rgba(0,212,255,0.04);border:1px solid rgba(45,53,97,0.6);border-radius:10px;flex-wrap:wrap;}}
.pnl-cal-summary-item{{display:flex;flex-direction:column;gap:2px;}}
.pnl-cal-summary-label{{font-size:0.72em;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;font-weight:600;}}
.pnl-cal-summary-value{{font-size:1em;font-weight:700;font-family:'Courier New',monospace;color:var(--cyan);}}
.pnl-cal-nodata{{text-align:center;padding:40px 20px;color:var(--text-dim);font-size:0.9em;}}

/* FOOTER */
.footer{{text-align:center;padding:32px 0;border-top:1px solid var(--border);margin-top:40px;font-size:0.85em;color:var(--text-dim);}}

/* LAST REFRESH BADGE (top right, fixed) */
.last-refresh-badge{{
  position:fixed;top:16px;right:24px;z-index:200;
  background:rgba(26,31,58,0.95);backdrop-filter:blur(10px);
  border:1px solid var(--border);border-radius:10px;
  padding:10px 16px;font-size:0.8em;color:var(--text-dim);
  box-shadow:0 4px 14px rgba(0,0,0,0.3);display:flex;align-items:center;gap:10px;
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
@media(max-width:900px){{.last-refresh-badge{{top:10px;right:10px;font-size:0.72em;padding:8px 12px;}}}}

@media(max-width:1200px){{
  .sidebar{{width:70px;}}
  .sidebar-header h1,.sidebar-header p,.nav-item span.label,.sidebar-footer{{display:none;}}
  .nav-item{{justify-content:center;padding:12px;}}
  .nav-item span.icon{{margin:0;}}
  .main-content{{margin-left:70px;padding:20px;}}
  .chart-grid,.portfolio-grid{{grid-template-columns:1fr;}}
  .bot-grid{{grid-template-columns:1fr;}}
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

  <!-- LIVE PAPER ACCOUNT (populated via JS from broker) -->
  <h3 style="color:var(--cyan);margin-bottom:14px;font-size:1em;text-transform:uppercase;letter-spacing:1px;">💵 Your Paper Account</h3>
  <div class="cards-row">
    <div class="card">
      <div class="card-label">Account Value</div>
      <div id="ovEquity" class="card-value">$—</div>
      <div class="card-sub">Cash + positions market value</div>
    </div>
    <div class="card">
      <div class="card-label">Total P&L</div>
      <div id="ovTotalPL" class="card-value">$—</div>
      <div id="ovTotalPLsub" class="card-sub">vs starting capital</div>
    </div>
    <div class="card">
      <div class="card-label">Starting Capital</div>
      <div id="ovStart" class="card-value">$1,000</div>
      <div class="card-sub">Profits reinvest on next rebalance</div>
    </div>
    <div class="card">
      <div class="card-label">Cash Available</div>
      <div id="ovCash" class="card-value">$—</div>
      <div class="card-sub">To deploy</div>
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

  <!-- STRATEGY METRICS (from backtest history, not paper account) -->
  <h3 style="color:var(--text-dim);margin-top:12px;margin-bottom:14px;font-size:1em;text-transform:uppercase;letter-spacing:1px;">📈 Strategy Metrics <span style="font-weight:400;font-size:0.8em;color:var(--text-dim);">— aggregated across all {total} bots (backtest data, not your paper account)</span></h3>
  <div class="cards-row">
    <div class="card">
      <div class="card-label">Active Bots</div>
      <div class="card-value">{active_count}</div>
      <div class="card-sub">{paused_count} paused · {total} total tracked</div>
    </div>
    <div class="card">
      <div class="card-label">Backtest Cumulative P&L</div>
      <div class="card-value" style="color:var(--{pnl_color});font-size:1.6em;">${total_pnl:,.0f} {pnl_arrow}</div>
      <div class="card-sub">Sum of 30-day strategy backtests</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Win Rate</div>
      <div class="card-value">{avg_wr:.1f}%</div>
      <div class="card-sub">Across all bots</div>
    </div>
    <div class="card">
      <div class="card-label">Avg Profit Factor</div>
      <div class="card-value">{avg_pf:.2f}</div>
      <div class="card-sub">Target &gt; 1.20</div>
    </div>
    <div class="card">
      <div class="card-label">Verdicts</div>
      <div class="card-value" style="font-size:1em;">
        <span style="color:var(--red);">{vc.get('PAUSE',0)}P</span> ·
        <span style="color:var(--amber);">{vc.get('HOLD',0)}H</span> ·
        <span style="color:var(--lime);">{vc.get('REACTIVATE',0)}R</span>
      </div>
      <div class="card-sub">Pause · Hold · Reactivate</div>
    </div>
    <div class="card">
      <div class="card-label">Expected Monthly</div>
      <div id="ovExpReturn" class="card-value" style="color:var(--lime);font-size:1.3em;">—</div>
      <div class="card-sub">⚠️ Estimate only, not guaranteed</div>
    </div>
  </div>

  <div class="chart-grid">
    <div class="chart-box">
      <h3>Backtest P&L by Strategy</h3>
      <canvas id="overviewPnlChart"></canvas>
    </div>
    <div class="chart-box">
      <h3>Verdict Distribution</h3>
      <canvas id="verdictPieChart"></canvas>
    </div>
  </div>
  <!-- P&L CALENDAR -->
  <div class="pnl-calendar" id="pnlCalendarSection">
    <div class="pnl-calendar-header">
      <h3>📅 Daily P&L Calendar</h3>
      <div class="pnl-calendar-nav">
        <button onclick="calPrev()">◀ Prev</button>
        <span class="cal-month-label" id="calMonthLabel">—</span>
        <button onclick="calNext()">Next ▶</button>
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
        <span class="pnl-cal-summary-label">Monthly P&L</span>
        <span class="pnl-cal-summary-value" id="calSumPnl">—</span>
      </div>
      <div class="pnl-cal-summary-item">
        <span class="pnl-cal-summary-label">Monthly %</span>
        <span class="pnl-cal-summary-value" id="calSumPct">—</span>
      </div>
      <div class="pnl-cal-summary-item">
        <span class="pnl-cal-summary-label">Best Day</span>
        <span class="pnl-cal-summary-value" id="calSumBest">—</span>
      </div>
      <div class="pnl-cal-summary-item">
        <span class="pnl-cal-summary-label">Worst Day</span>
        <span class="pnl-cal-summary-value" id="calSumWorst">—</span>
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
        portfolio = portfolio or {}
        allocations = portfolio.get("allocations", [])
        excluded = portfolio.get("excluded", [])
        summary = portfolio.get("summary", {})
        total_capital = summary.get("total_capital", 1000)
        total_allocated = summary.get("allocated", sum(a.get("allocation_usd", 0) for a in allocations))

        # Build allocation table rows using correct keys from portfolio_allocator.py
        alloc_rows = ""
        for a in allocations:
            name = a.get("bot_name", a.get("strategy", "Unknown"))
            pair = a.get("pair", "N/A")
            usd = a.get("allocation_usd", a.get("allocated_usd", 0))
            usd = abs(usd) if abs(usd) < 0.005 else usd  # avoid $-0.00
            pct = a.get("allocation_pct", 0)
            exp_ret = a.get("expected_monthly_return", 0)
            exp_ret = abs(exp_ret) if abs(exp_ret) < 0.005 else exp_ret  # avoid $-0.00
            score = a.get("score", 0)
            reasoning = a.get("reasoning", "")
            alloc_rows += f"""<tr>
      <td><strong>{name}</strong></td>
      <td>{pair}</td>
      <td style="color:var(--cyan);font-family:'Courier New',monospace;">${usd:,.2f}</td>
      <td>{pct:.1f}%</td>
      <td style="color:var(--lime);">${exp_ret:,.2f}</td>
      <td>{score:.0f}</td>
      <td style="font-size:0.82em;color:var(--text-dim);">{reasoning}</td>
    </tr>"""

        # Build excluded section
        excluded_html = ""
        if excluded:
            excluded_html = '<div class="portfolio-excluded"><h3>⚠️ Excluded Strategies</h3>'
            for exc in excluded:
                ename = exc.get("bot_name", exc.get("strategy", "Unknown"))
                excluded_html += f'<div class="excluded-item"><strong>{ename}</strong>: {exc.get("reason", "N/A")}</div>'
            excluded_html += "</div>"

        div_score = summary.get("diversification_score", 0)
        exp_monthly_usd = summary.get("expected_monthly_return_usd", 0)
        exp_monthly_pct = summary.get("expected_monthly_return_pct", 0)
        n_strats = summary.get("num_strategies", len(allocations))

        return f"""<div class="page" id="portfolio">
  <div class="page-title"><span class="accent">💼</span> Portfolio Allocation</div>
  <div class="portfolio-hero">
    <div class="portfolio-hero-subtitle">Starting Capital</div>
    <div class="portfolio-hero-value">${total_capital:,.0f}</div>
    <div class="portfolio-hero-subtitle">Allocated across {n_strats} strategies</div>
  </div>

  <!-- Bot Activity Timeline (Gantt) -->
  <div class="chart-box" style="margin-bottom:28px;">
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:14px;">
      <h3>📊 Bot Activity Timeline</h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
        <label style="font-size:0.8em;color:var(--text-dim);">From:</label>
        <input type="date" id="ganttFrom" style="background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:0.85em;" />
        <label style="font-size:0.8em;color:var(--text-dim);">To:</label>
        <input type="date" id="ganttTo" style="background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:6px;padding:5px 10px;font-size:0.85em;" />
        <button class="filter-btn" onclick="ganttApplyDates()" style="padding:6px 14px;">Apply</button>
      </div>
    </div>
    <div id="ganttContainer" style="overflow-x:auto;min-height:200px;">
      <div style="padding:30px;text-align:center;color:var(--text-dim);font-size:0.9em;">Connect to the paper broker to see bot activity timeline.</div>
    </div>
  </div>

  <h3 style="margin-bottom:16px;">Allocation Breakdown</h3>
  <table class="data-table" style="margin-bottom:28px;">
    <thead><tr>
      <th>Strategy</th><th>Pair</th><th>Allocated</th><th>%</th>
      <th>Est. Monthly</th><th>Score</th><th>Reasoning</th>
    </tr></thead>
    <tbody>{alloc_rows}</tbody>
  </table>
  {excluded_html}
  <div class="portfolio-summary">
    <h3 style="margin-bottom:16px;">Portfolio Summary</h3>
    <div class="summary-row"><span class="summary-label">Total Allocated</span><span class="summary-value">${total_allocated:,.2f}</span></div>
    <div class="summary-row"><span class="summary-label">Expected Monthly Return</span><span class="summary-value">${exp_monthly_usd:,.2f} ({exp_monthly_pct:+.1f}%) ⚠️ estimate only</span></div>
    <div class="summary-row"><span class="summary-label">Diversification Score</span><span class="summary-value">{div_score:.0f} / 100</span></div>
    <div class="summary-row"><span class="summary-label">Strategies Included</span><span class="summary-value">{n_strats}</span></div>
    <div class="summary-row"><span class="summary-label">Strategies Excluded</span><span class="summary-value">{len(excluded)}</span></div>
  </div>
  <div class="disclaimer">⚠️ Past performance does not guarantee future results. This allocation is for educational purposes only.</div>
</div>"""

    # ── PAGE: PAPER TRADING ──────────────────────────────────────────────
    def _page_alpaca(self):
        return """<div class="page" id="alpaca">
  <div class="page-title"><span class="accent">💵</span> Paper Trading</div>

  <!-- Simulator Status -->
  <div id="alpacaConnCard" class="card" style="margin-bottom:24px;">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px;">
      <div>
        <div class="card-label">Simulator Status</div>
        <div id="alpacaConnStatus" class="card-value" style="font-size:1.3em;color:var(--gray);">⚪ Not Initialized</div>
        <div id="alpacaConnMsg" class="card-sub">Click Connect to initialize the $1,000 paper account (synthetic math-based pricing)</div>
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;">
        <button class="filter-btn" style="padding:12px 22px;font-weight:600;" onclick="alpacaConnect()">🔌 Connect</button>
        <button class="filter-btn" style="padding:12px 22px;font-weight:600;" onclick="alpacaConfirmReset()">🔄 Reset to $1000</button>
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
            When enabled, the system re-analyzes all bots every 30 minutes and automatically rebalances your paper portfolio. No clicks needed — just check the dashboard each morning.
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
          <div id="autoInterval" style="font-family:'Courier New',monospace;color:var(--cyan);font-weight:600;">30 min</div>
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
      <table class="data-table" id="alpPreviewTable">
        <thead><tr>
          <th>Bot</th><th>Symbol</th><th>Side</th><th>Notional</th>
          <th>Target</th><th>Current</th><th>Status</th>
        </tr></thead>
        <tbody id="alpPreviewBody"></tbody>
      </table>
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
    ⚠️ Paper trading only — no real money is involved. Starts with $1,000 virtual capital. Positions are valued with an internal math model, and all orders are simulated locally. The "expected monthly return" on the Portfolio page is a projection from historical backtests, not a guarantee.
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
  <div class="page-title"><span class="accent">🔗</span> Alpaca Paper Trading</div>
  <p class="page-sub" style="color:var(--text-dim);margin-bottom:24px;">Connect to your real Alpaca paper trading account with live market prices</p>

  <!-- Broker Selector -->
  <div class="card" style="margin-bottom:24px;">
    <div style="display:flex;align-items:center;gap:16px;margin-bottom:16px;">
      <span style="font-size:1.2em;font-weight:600;color:var(--text);">Select Broker</span>
    </div>
    <div style="display:flex;gap:12px;flex-wrap:wrap;">
      <button id="brokerAlpacaBtn" onclick="alpLiveSelectBroker('alpaca')" class="filter-btn"
              style="padding:14px 24px;font-size:1em;border-radius:10px;display:flex;align-items:center;gap:10px;min-width:200px;background:rgba(0,212,255,0.1);border-color:var(--cyan);color:var(--cyan);">
        <span style="font-size:1.5em;">🦙</span>
        <span>
          <strong>Alpaca Paper Trading</strong><br>
          <span style="font-size:0.8em;opacity:0.7;">Crypto &amp; stocks · free paper account</span>
        </span>
      </button>
      <button id="brokerCoinbaseBtn" disabled class="filter-btn"
              style="padding:14px 24px;font-size:1em;border-radius:10px;display:flex;align-items:center;gap:10px;min-width:200px;opacity:0.4;cursor:not-allowed;">
        <span style="font-size:1.5em;">🪙</span>
        <span>
          <strong>Coinbase</strong><br>
          <span style="font-size:0.8em;opacity:0.7;">Coming soon · real money trading</span>
        </span>
      </button>
    </div>
  </div>

  <!-- Connection Card -->
  <div id="alpLiveConnCard" class="card" style="margin-bottom:24px;">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
      <div>
        <div style="font-size:0.9em;color:var(--text-dim);margin-bottom:4px;">Connection Status</div>
        <div id="alpLiveConnStatus" class="card-value" style="font-size:1.3em;color:var(--gray);">⚪ Not Connected</div>
        <div id="alpLiveConnMsg" class="card-sub">Click Connect to link your Alpaca paper trading account</div>
      </div>
      <button id="alpLiveConnBtn" onclick="alpLiveConnect()" class="filter-btn"
              style="padding:12px 28px;font-size:1em;background:rgba(0,212,255,0.15);border-color:var(--cyan);color:var(--cyan);font-weight:600;border-radius:8px;">
        🔗 Connect
      </button>
    </div>
  </div>

  <!-- Account Summary (hidden until connected) -->
  <div id="alpLiveAccountSection" style="display:none;">
    <div class="stats-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;margin-bottom:24px;">
      <div class="card"><div class="card-sub">Equity</div><div id="alpLiveEquity" class="card-value">—</div></div>
      <div class="card"><div class="card-sub">Cash (Buying Power)</div><div id="alpLiveCash" class="card-value">—</div></div>
      <div class="card"><div class="card-sub">Today's P&L</div><div id="alpLivePL" class="card-value">—</div></div>
      <div class="card"><div class="card-sub">Account #</div><div id="alpLiveAccNum" class="card-value" style="font-size:0.95em;">—</div></div>
    </div>

    <!-- Positions -->
    <div class="card" style="margin-bottom:24px;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <span style="font-weight:600;font-size:1.1em;">Open Positions</span>
        <button onclick="alpLiveRefreshPositions()" class="filter-btn" style="padding:6px 14px;font-size:0.85em;">Refresh</button>
      </div>
      <div id="alpLivePositionsEmpty" style="color:var(--text-dim);padding:20px 0;text-align:center;">No open positions</div>
      <div id="alpLivePositionsTable" style="display:none;overflow-x:auto;">
        <table class="data-table">
          <thead><tr>
            <th>Symbol</th><th>Qty</th><th>Avg Entry</th><th>Current</th>
            <th>Market Value</th><th>P&L</th><th>P&L %</th><th>Action</th>
          </tr></thead>
          <tbody id="alpLivePositionsBody"></tbody>
        </table>
      </div>
      <div id="alpLivePosSummary" style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);display:none;">
        <span style="color:var(--text-dim);font-size:0.9em;">Total: </span>
        <span id="alpLivePosTotal" style="font-weight:600;">—</span>
        <button onclick="alpLiveCloseAll()" class="filter-btn" style="margin-left:16px;padding:6px 14px;font-size:0.85em;color:var(--red);border-color:var(--red);">Close All</button>
      </div>
    </div>

    <!-- Quick Trade -->
    <div class="card" style="margin-bottom:24px;">
      <div style="font-weight:600;font-size:1.1em;margin-bottom:12px;">Quick Trade</div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:end;">
        <div>
          <label style="font-size:0.8em;color:var(--text-dim);">Symbol</label>
          <input id="alpLiveTradeSymbol" type="text" placeholder="BTC/USD" style="display:block;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:6px;width:120px;margin-top:4px;">
        </div>
        <div>
          <label style="font-size:0.8em;color:var(--text-dim);">Amount (USD)</label>
          <input id="alpLiveTradeAmount" type="number" placeholder="100" min="1" step="1" style="display:block;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:6px;width:120px;margin-top:4px;">
        </div>
        <button onclick="alpLiveTrade('buy')" class="filter-btn" style="padding:8px 20px;background:rgba(57,255,20,0.12);color:var(--lime);border-color:var(--lime);font-weight:600;">Buy</button>
        <button onclick="alpLiveTrade('sell')" class="filter-btn" style="padding:8px 20px;background:rgba(255,69,58,0.12);color:var(--red);border-color:var(--red);font-weight:600;">Sell</button>
      </div>
      <div id="alpLiveTradeResult" style="margin-top:12px;padding:10px;border-radius:6px;display:none;"></div>
    </div>

    <!-- Recent Orders -->
    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;">
        <span style="font-weight:600;font-size:1.1em;">Recent Orders</span>
        <button onclick="alpLiveRefreshOrders()" class="filter-btn" style="padding:6px 14px;font-size:0.85em;">Refresh</button>
      </div>
      <div id="alpLiveOrdersEmpty" style="color:var(--text-dim);padding:20px 0;text-align:center;">No recent orders</div>
      <div id="alpLiveOrdersTable" style="display:none;overflow-x:auto;">
        <table class="data-table">
          <thead><tr>
            <th>Time</th><th>Symbol</th><th>Side</th><th>Amount</th>
            <th>Fill Price</th><th>Status</th>
          </tr></thead>
          <tbody id="alpLiveOrdersBody"></tbody>
        </table>
      </div>
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
            sr = self._num(ed.get("sharpe_ratio", 0))
            md = self._num(ed.get("max_drawdown", 0))
            adapt = self._num(ed.get("adaptation_score", 0))
            rows += f"""<tr class="row-{v.lower()}" data-verdict="{v}">
      <td><strong>{bot_name}</strong></td>
      <td><span class="badge badge-{v.lower()}">{v}</span></td>
      <td><span class="tip" data-tip="How often trades are profitable. Above 50% is good.">{wr:.1f}%{self._quality_badge('wr', wr)}</span></td>
      <td><span class="tip" data-tip="Gross profit ÷ gross loss. Above 1.0 means profitable overall.">{pf:.2f}{self._quality_badge('pf', pf)}</span></td>
      <td><span class="tip" data-tip="Return per unit of risk. Higher is better — 1.0+ is great.">{sr:.2f}{self._quality_badge('sharpe', sr)}</span></td>
      <td><span class="tip" data-tip="Biggest drop from a peak. Lower is safer.">{md:.1f}%{self._quality_badge('dd', md)}</span></td>
      <td><span class="tip" data-tip="How well this strategy fits current market conditions. 75+ is great.">{adapt:.0f}/100{self._quality_badge('adapt', adapt)}</span></td>
    </tr>"""

        return f"""<div class="page" id="quantum">
  <div class="page-title"><span class="accent">⚛️</span> Strategy Scorecard</div>
  <div class="filter-buttons">
    <button class="filter-btn active" onclick="filterQuantum('ALL')">Show All</button>
    <button class="filter-btn" onclick="filterQuantum('PAUSE')">Pause</button>
    <button class="filter-btn" onclick="filterQuantum('HOLD')">Hold</button>
    <button class="filter-btn" onclick="filterQuantum('REACTIVATE')">Reactivate</button>
  </div>
  <table class="data-table" id="quantumTable">
    <thead><tr>
      <th onclick="sortTable(0)">Strategy</th>
      <th onclick="sortTable(1)">Verdict</th>
      <th onclick="sortTable(2)">Win Rate</th>
      <th onclick="sortTable(3)">Profit Factor</th>
      <th onclick="sortTable(4)">Risk-Adj. Return</th>
      <th onclick="sortTable(5)">Worst Drop</th>
      <th onclick="sortTable(6)">Market Fit</th>
    </tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

    # ── PAGE: BOT STATUS ─────────────────────────────────────────────────
    def _page_bots(self, bots_data, evaluations):
        cards = ""
        for bot in bots_data:
            name = bot.get("name", "Unknown")
            pair = bot.get("pair", "N/A")
            status = bot.get("status", "unknown")
            # Pull real metrics from evaluations
            ed = self._evaluation_for_bot(bot, evaluations)
            status = ed.get("bot_status", status)
            pnl = self._num(ed.get("net_profit", 0))
            wr = self._num(ed.get("win_rate", 0))
            trades = self._num(ed.get("total_trades", 0))
            pf = self._num(ed.get("profit_factor", 0))
            verdict = ed.get("verdict", "HOLD")
            adapt = self._num(ed.get("adaptation_score", 50))

            pulse = '<span class="status-pulse"></span>' if status == "active" else ""
            status_color = "lime" if status == "active" else "amber" if status == "paused" else "red"
            pnl_color = "lime" if pnl >= 0 else "red"

            cards += f"""<div class="bot-card">
      <div class="bot-card-header">
        <div>
          <div class="bot-card-name">{name}{pulse}</div>
          <div class="bot-card-pair">{pair}</div>
        </div>
        <span class="badge badge-{verdict.lower()}">{verdict}</span>
      </div>
      <div class="bot-card-metrics">
        <div><div class="bot-card-metric-label">P&L</div><div class="bot-card-metric-val" style="color:var(--{pnl_color});">${pnl:,.0f}</div></div>
        <div><div class="bot-card-metric-label">Win Rate</div><div class="bot-card-metric-val">{wr:.1f}%{self._quality_badge('wr', wr)}</div></div>
        <div><div class="bot-card-metric-label">Profit Factor</div><div class="bot-card-metric-val">{pf:.2f}{self._quality_badge('pf', pf)}</div></div>
        <div><div class="bot-card-metric-label">Trades</div><div class="bot-card-metric-val">{trades:.0f}</div></div>
        <div><div class="bot-card-metric-label">Market Fit</div><div class="bot-card-metric-val">{adapt:.0f}/100{self._quality_badge('adapt', adapt)}</div></div>
        <div><div class="bot-card-metric-label">Current Status</div><div class="bot-card-metric-val" style="color:var(--{status_color});">● {status.upper()}</div></div>
      </div>
    </div>"""

        return f"""<div class="page" id="bots">
  <div class="page-title"><span class="accent">🤖</span> Bot Status</div>
  <div class="bot-grid">{cards}</div>
</div>"""

    # ── PAGE: PERFORMANCE ────────────────────────────────────────────────
    def _page_performance(self, evaluations):
        return """<div class="page" id="performance">
  <div class="page-title"><span class="accent">📈</span> Performance Analytics</div>
  <div class="chart-grid">
    <div class="chart-box"><h3>Cumulative P&L (Top Strategies)</h3><canvas id="pnlChart"></canvas></div>
    <div class="chart-box"><h3>Win Rate Distribution</h3><canvas id="winrateChart"></canvas></div>
  </div>
  <div class="chart-grid">
    <div class="chart-box"><h3>Risk vs Return (Sharpe vs Profit Factor)</h3><canvas id="riskreturnChart"></canvas></div>
    <div class="chart-box"><h3>Top Strategy Breakdown</h3><canvas id="radarChart"></canvas></div>
  </div>
</div>"""

    # ── PAGE: LEARNING ENGINE ────────────────────────────────────────────
    def _page_learning(self, learning_stats, evaluations):
        cards = ""
        for bot_name, stats in learning_stats.items():
            if bot_name.startswith("_"):
                continue
            adapt = stats.get("adaptation_score", 0)
            if adapt >= 75:
                sc, label = "score-well", "Excellent"
            elif adapt >= 60:
                sc, label = "score-moderate", "Good"
            elif adapt >= 40:
                sc, label = "score-neutral", "Fair"
            else:
                sc, label = "score-poor", "Poor"
            bar_color = "lime" if adapt >= 75 else "cyan" if adapt >= 60 else "amber" if adapt >= 40 else "red"

            cards += f"""<div class="adapt-card">
      <div style="font-weight:600;">{bot_name}</div>
      <div class="adapt-score {sc}">{adapt:.0f}</div>
      <div class="adapt-label">{label}</div>
      <div class="progress-bar" style="margin-top:10px;">
        <div class="progress-fill" style="width:{adapt}%;background:var(--{bar_color});"></div>
      </div>
    </div>"""

        return f"""<div class="page" id="learning">
  <div class="page-title"><span class="accent">🧠</span> Learning Engine</div>
  <div class="adapt-cards">{cards}</div>
  <div class="chart-grid">
    <div class="chart-box"><h3>Adaptation Score Distribution</h3><canvas id="adaptationChart"></canvas></div>
    <div class="chart-box"><h3>Adaptation vs Win Rate</h3><canvas id="adaptWinrateChart"></canvas></div>
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
  <div class="page-title"><span class="accent">🌊</span> Market Regime Analysis</div>
  <div class="regime-badge-large regime-{regime}">
    {emoji} {regime.replace('_',' ').title()} — {confidence:.0f}% Confidence
  </div>
  <div class="cards-row">
    <div class="card"><div class="card-label">Volatility</div><div class="card-value" style="font-size:1.2em;">{vol}</div><div class="card-sub">How wildly prices are swinging</div></div>
    <div class="card"><div class="card-label">Trend Direction</div><div class="card-value" style="font-size:1.2em;">{trend}</div><div class="card-sub">Positive = prices going up</div></div>
    <div class="card"><div class="card-label">Autocorrelation</div><div class="card-value" style="font-size:1.2em;">{autocorr}</div><div class="card-sub">Do trends tend to continue? (closer to 1 = yes)</div></div>
    <div class="card"><div class="card-label">Vol Ratio</div><div class="card-value" style="font-size:1.2em;">{vol_ratio}</div><div class="card-sub">Current vs historical volatility</div></div>
  </div>
  <div class="chart-grid">
    <div class="chart-box"><h3>Strategy Scores by Regime Fit</h3><canvas id="regimeChart"></canvas></div>
    <div class="chart-box"><h3>Confidence Radar</h3><canvas id="confidenceChart"></canvas></div>
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
  <div class="page-title"><span class="accent">📋</span> Decision Log</div>
  <div class="decision-timeline">{items}</div>
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
  var normalized = /([zZ]|[+-]\d\d:\d\d)$/.test(iso) ? iso : iso + 'Z';
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
// Refresh the badge when page loads and every 60s
loadLastRefresh();
setInterval(loadLastRefresh, 60000);

// ── Live Overview Paper Account Stats ─────────────────────────
async function loadOverviewAccount() {{
  try {{
    var r = await fetch('/api/broker/connect');
    var d = await r.json();
    if (!d.connected || !d.account) return;
    var a = d.account;
    var fmt = function(n) {{ return '$' + Number(n || 0).toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}}); }};
    var setText = function(id, val) {{ var el = document.getElementById(id); if(el) el.textContent = val; }};
    setText('ovEquity', fmt(a.equity));
    setText('ovCash', fmt(a.cash));
    setText('ovStart', fmt(a.starting_balance));
    var plPct = a.total_pl_pct || 0;
    var plEl = document.getElementById('ovTotalPL');
    if (plEl) {{
      plEl.textContent = (a.total_pl >= 0 ? '+' : '') + fmt(a.total_pl);
      plEl.style.color = a.total_pl >= 0 ? 'var(--lime)' : 'var(--red)';
    }}
    var plSub = document.getElementById('ovTotalPLsub');
    if (plSub) plSub.textContent = (plPct >= 0 ? '+' : '') + plPct.toFixed(2) + '% from starting capital';
    // Position count
    var pr = await fetch('/api/broker/positions');
    var pd = await pr.json();
    if (pd.summary) setText('ovPositions', pd.summary.count);
    // Expected monthly from status
    var sr = await fetch('/api/status');
    var sd = await sr.json();
    if (sd.expected_monthly_return_pct !== undefined) {{
      setText('ovExpReturn', '+' + (sd.expected_monthly_return_pct || 0).toFixed(1) + '%');
    }}
  }} catch (e) {{ /* server not running — leave placeholders */ }}
}}
loadOverviewAccount();
setInterval(loadOverviewAccount, 30000);

// ── Navigation ──────────────────────────────────────────────────
var allPages = {json.dumps([p[0] for p in self.pages])};

function showPage(name) {{
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
}}

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
    var aNum = parseFloat(aText.replace(/[^\\d.\\-]/g, ''));
    var bNum = parseFloat(bText.replace(/[^\\d.\\-]/g, ''));
    if (!isNaN(aNum) && !isNaN(bNum)) return asc ? aNum - bNum : bNum - aNum;
    return asc ? aText.localeCompare(bText) : bText.localeCompare(aText);
  }});
  rows.forEach(function(r) {{ tbody.appendChild(r); }});
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
  return '$' + val.toLocaleString(undefined, {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
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
    body.innerHTML = '<table class="data-table"><thead><tr>' +
      '<th>Symbol</th><th>Qty</th><th>Avg Entry</th><th>Current</th>' +
      '<th>Market Value</th><th>Unrealized P&L</th><th>%</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
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
    body.innerHTML = '<table class="data-table"><thead><tr>' +
      '<th>Time</th><th>Symbol</th><th>Side</th><th>Notional/Qty</th>' +
      '<th>Fill Price</th><th>Status</th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
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
    document.getElementById('autoInterval').textContent = (s.interval_min || 30) + ' min';
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
    alpMsg(newState ? '🤖 Auto-trading ENABLED — first cycle will run within 30 min' : '⏸️ Auto-trading DISABLED',
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

// Auto-refresh status every 30s
autoRefresh();
setInterval(autoRefresh, 30000);

// Auto-connect when user clicks the Alpaca tab for the first time
var alpacaAutoTried = false;
document.addEventListener('DOMContentLoaded', function() {{
  var initialPage = document.querySelector('.page.active');
  var initialPageId = initialPage ? initialPage.id : 'overview';
  ensureChartsForPage(initialPageId);
  // Re-trigger after short delay to handle layout stabilization
  setTimeout(function() {{ ensureChartsForPage(initialPageId); }}, 200);
  setTimeout(function() {{ ensureChartsForPage(initialPageId); }}, 800);
  var alpacaNav = document.querySelector('.nav-item[data-page="alpaca"]');
  if (alpacaNav) {{
    alpacaNav.addEventListener('click', function() {{
      if (!alpacaAutoTried && !alpacaConnected) {{
        alpacaAutoTried = true;
        setTimeout(alpacaConnect, 300);
      }}
    }});
  }}
}});

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
    var data = await apiGet('/api/broker/orders?limit=500');
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
    if (!o.symbol || !o.submitted_at) return;
    if (!symbols[o.symbol]) symbols[o.symbol] = [];
    symbols[o.symbol].push(o);
  }});

  // Build spans: buy opens a span, sell closes it
  var spans = {{}};
  Object.keys(symbols).forEach(function(sym) {{
    var symOrders = symbols[sym].sort(function(a,b) {{ return a.submitted_at.localeCompare(b.submitted_at); }});
    spans[sym] = [];
    var currentSpan = null;
    symOrders.forEach(function(o) {{
      var t = new Date(o.submitted_at);
      if (o.side === 'buy') {{
        if (!currentSpan) {{
          currentSpan = {{ start: t, entryPrice: o.filled_avg_price || 0, symbol: sym }};
        }}
      }} else if (o.side === 'sell' && currentSpan) {{
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
var calYear = new Date().getFullYear();
var calMonth = new Date().getMonth(); // 0-indexed

async function calLoadData() {{
  try {{
    var resp = await fetch('/api/broker/daily-pnl');
    var json = await resp.json();
    calData = json.snapshots || {{}};
    calRender();
  }} catch (e) {{
    calRender(); // render empty
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
  // Keep the day headers (first 7 children)
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

  // Collect daily P&L changes (compare each day to previous day)
  var sortedDates = Object.keys(calData).sort();
  var dailyChanges = {{}};
  for (var i = 0; i < sortedDates.length; i++) {{
    var d = sortedDates[i];
    var snap = calData[d];
    var prevEquity = snap.starting_balance || 1000;
    // Find previous day's equity
    if (i > 0) {{
      prevEquity = calData[sortedDates[i-1]].equity || prevEquity;
    }}
    var dayPnl = (snap.equity || 0) - prevEquity;
    var dayPct = prevEquity > 0 ? (dayPnl / prevEquity * 100) : 0;
    dailyChanges[d] = {{ pnl: dayPnl, pct: dayPct, equity: snap.equity }};
  }}

  // Empty cells before first day
  for (var e = 0; e < firstDay; e++) {{
    var empty = document.createElement('div');
    empty.className = 'pnl-cal-cell empty';
    grid.appendChild(empty);
  }}

  var monthPnl = 0;
  var bestDay = null;
  var worstDay = null;
  var daysTracked = 0;
  var firstEquity = null;
  var lastEquity = null;

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

      var pnlEl = document.createElement('div');
      pnlEl.className = 'cal-pnl-usd';
      pnlEl.textContent = (change.pnl >= 0 ? '+$' : '-$') + Math.abs(change.pnl).toFixed(2);
      cell.appendChild(pnlEl);

      var pctEl = document.createElement('div');
      pctEl.className = 'cal-pnl-pct';
      pctEl.textContent = (change.pct >= 0 ? '+' : '') + change.pct.toFixed(2) + '%';
      cell.appendChild(pctEl);

      if (change.pnl > 0) cell.classList.add('positive');
      else if (change.pnl < 0) cell.classList.add('negative');
      else cell.classList.add('zero');

      if (bestDay === null || change.pnl > bestDay.pnl) bestDay = {{ pnl: change.pnl, date: dateStr }};
      if (worstDay === null || change.pnl < worstDay.pnl) worstDay = {{ pnl: change.pnl, date: dateStr }};
    }}

    grid.appendChild(cell);
  }}

  // Summary
  var summaryEl = document.getElementById('calSummary');
  if (daysTracked > 0) {{
    summaryEl.style.display = 'flex';
    var pnlColor = monthPnl >= 0 ? 'var(--lime)' : 'var(--red)';
    var monthPct = firstEquity > 0 ? (monthPnl / firstEquity * 100) : 0;
    document.getElementById('calSumPnl').textContent = (monthPnl >= 0 ? '+$' : '-$') + Math.abs(monthPnl).toFixed(2);
    document.getElementById('calSumPnl').style.color = pnlColor;
    document.getElementById('calSumPct').textContent = (monthPct >= 0 ? '+' : '') + monthPct.toFixed(2) + '%';
    document.getElementById('calSumPct').style.color = pnlColor;
    document.getElementById('calSumBest').textContent = bestDay ? ((bestDay.pnl >= 0 ? '+$' : '-$') + Math.abs(bestDay.pnl).toFixed(2)) : '—';
    document.getElementById('calSumBest').style.color = bestDay && bestDay.pnl >= 0 ? 'var(--lime)' : 'var(--red)';
    document.getElementById('calSumWorst').textContent = worstDay ? ((worstDay.pnl >= 0 ? '+$' : '-$') + Math.abs(worstDay.pnl).toFixed(2)) : '—';
    document.getElementById('calSumWorst').style.color = worstDay && worstDay.pnl >= 0 ? 'var(--lime)' : 'var(--red)';
    document.getElementById('calSumDays').textContent = daysTracked;
  }} else {{
    summaryEl.style.display = 'none';
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
      alpLiveUpdateAccount(data.account);
      alpLiveRefreshPositions();
      alpLiveRefreshOrders();
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
  document.getElementById('alpLiveEquity').textContent = fmtUSD(acct.equity);
  document.getElementById('alpLiveCash').textContent = fmtUSD(acct.cash);
  var pl = acct.total_pl || 0;
  var plEl = document.getElementById('alpLivePL');
  plEl.textContent = (pl >= 0 ? '+' : '') + fmtUSD(pl);
  plEl.style.color = pl >= 0 ? 'var(--lime)' : 'var(--red)';
  document.getElementById('alpLiveAccNum').textContent = acct.account_number || '—';
}}

async function alpLiveRefreshPositions() {{
  if (!alpLiveConnected) return;
  try {{
    var data = await apiGet('/api/alpaca/positions');
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
      var plColor = p.unrealized_pl >= 0 ? 'var(--lime)' : 'var(--red)';
      var plSign = p.unrealized_pl >= 0 ? '+' : '';
      body.innerHTML += '<tr>' +
        '<td style="font-weight:600;color:var(--cyan);">' + p.symbol + '</td>' +
        '<td>' + Number(p.qty).toFixed(4) + '</td>' +
        '<td>' + fmtUSD(p.avg_entry_price) + '</td>' +
        '<td>' + fmtUSD(p.current_price) + '</td>' +
        '<td>' + fmtUSD(p.market_value) + '</td>' +
        '<td style="color:' + plColor + ';">' + plSign + fmtUSD(p.unrealized_pl) + '</td>' +
        '<td style="color:' + plColor + ';">' + plSign + p.unrealized_plpc.toFixed(2) + '%</td>' +
        '<td><button onclick="alpLiveClosePos(\'' + p.symbol + '\')" class="filter-btn" style="padding:4px 10px;font-size:0.8em;color:var(--red);border-color:var(--red);">Close</button></td>' +
        '</tr>';
    }});
    if (data.summary) {{
      var s = data.summary;
      var tColor = s.total_unrealized_pl >= 0 ? 'var(--lime)' : 'var(--red)';
      document.getElementById('alpLivePosTotal').innerHTML =
        '<span style="color:var(--text);">' + s.count + ' positions · ' + fmtUSD(s.total_market_value) + '</span>' +
        ' · <span style="color:' + tColor + ';">' + (s.total_unrealized_pl >= 0 ? '+' : '') + fmtUSD(s.total_unrealized_pl) + '</span>';
    }}
    // Also refresh account
    var acct = await apiGet('/api/alpaca/account');
    alpLiveUpdateAccount(acct);
  }} catch(e) {{
    console.error('Positions refresh error:', e);
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
      var sideColor = o.side === 'buy' ? 'var(--lime)' : 'var(--red)';
      var time = o.submitted_at ? new Date(o.submitted_at).toLocaleString() : '—';
      var fillPrice = o.filled_avg_price ? fmtUSD(o.filled_avg_price) : '—';
      var amount = o.notional ? fmtUSD(o.notional) : (o.qty ? o.qty + ' units' : '—');
      body.innerHTML += '<tr>' +
        '<td style="font-size:0.85em;">' + time + '</td>' +
        '<td style="font-weight:600;color:var(--cyan);">' + o.symbol + '</td>' +
        '<td style="color:' + sideColor + ';font-weight:600;">' + (o.side || '').toUpperCase() + '</td>' +
        '<td>' + amount + '</td>' +
        '<td>' + fillPrice + '</td>' +
        '<td>' + (o.status || '—') + '</td>' +
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
      setTimeout(function() {{ alpLiveRefreshPositions(); alpLiveRefreshOrders(); }}, 2000);
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
    else {{ setTimeout(function() {{ alpLiveRefreshPositions(); alpLiveRefreshOrders(); }}, 2000); }}
  }} catch(e) {{ alert('Error: ' + e.message); }}
}}

async function alpLiveCloseAll() {{
  if (!confirm('Close ALL open Alpaca positions?')) return;
  try {{
    var data = await apiPost('/api/alpaca/close-all', {{ confirm: true }});
    setTimeout(function() {{ alpLiveRefreshPositions(); alpLiveRefreshOrders(); }}, 2000);
  }} catch(e) {{ alert('Error: ' + e.message); }}
}}

// Check Alpaca status on page load
alpLiveCheckStatus();

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
