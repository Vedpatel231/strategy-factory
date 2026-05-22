"""
Strategy Factory — Dashboard Generator (v4.0 — ETF Focused)
Clean 3-page dashboard: Overview, Alpaca, Claude Analysis.
Old pages hidden under Advanced dropdown.
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
    """Generates a single-file HTML dashboard — 3 main pages + Advanced."""

    def __init__(self):
        self.pages = [
            ("overview", "Overview", ""),
            ("alpaca-live", "Alpaca", ""),
            ("claude-analysis", "Claude Analysis", ""),
            # Advanced (hidden in dropdown)
            ("quantum", "Strategy Scorecard", ""),
            ("bots", "Bot Signals", ""),
            ("performance", "Performance", ""),
            ("learning", "Learning Engine", ""),
            ("regime", "Market Regime", ""),
            ("decisions", "Decision Log", ""),
            ("portfolio", "Portfolio", ""),
        ]

    def _num(self, value, default=0.0):
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def generate(self, bots_data, evaluations, regime_info, learning_stats, execution_summary, portfolio=None):
        """Build full HTML dashboard."""
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
                    "pair": ev.get("pair", ""),
                    "strategy_type": ev.get("strategy_type", ""),
                    "bot_status": ev.get("bot_status", "active"),
                    "reasons": ev.get("reasons", []),
                }
            evaluations = eval_dict

        timestamp = _now_est_label()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Strategy Factory — ETF Trading Dashboard</title>
<style>
/* ── RESET & BASE ── */
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg-primary:#0d1117;--bg-secondary:#161b22;--bg-card:#1c2128;--bg-hover:#21262d;
  --border:#30363d;--border-light:#484f58;
  --text-primary:#e6edf3;--text-secondary:#8b949e;--text-muted:#6e7681;
  --green:#22c55e;--green-dim:#16a34a;--red:#ef4444;--red-dim:#dc2626;
  --amber:#f59e0b;--blue:#3b82f6;--blue-dim:#2563eb;--purple:#a855f7;
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
}}
html{{background:var(--bg-primary);color:var(--text-primary);font-family:var(--font);font-size:14px;line-height:1.5}}
body{{min-height:100vh;display:flex;flex-direction:column}}
a{{color:var(--blue);text-decoration:none}}

/* ── NAV ── */
.top-nav{{background:var(--bg-secondary);border-bottom:1px solid var(--border);padding:0 24px;display:flex;align-items:center;height:56px;position:sticky;top:0;z-index:100}}
.nav-brand{{font-size:16px;font-weight:700;color:var(--text-primary);margin-right:32px;white-space:nowrap}}
.nav-brand span{{color:var(--blue);font-weight:400;font-size:12px;margin-left:8px;background:var(--bg-card);padding:2px 8px;border-radius:4px}}
.nav-links{{display:flex;gap:4px;flex:1}}
.nav-link{{padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:500;color:var(--text-secondary);transition:all .15s}}
.nav-link:hover{{background:var(--bg-hover);color:var(--text-primary)}}
.nav-link.active{{background:var(--blue-dim);color:#fff}}
.nav-dropdown{{position:relative}}
.nav-dropdown-menu{{display:none;position:absolute;top:100%;left:0;background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:4px;min-width:200px;box-shadow:0 8px 24px rgba(0,0,0,.4);z-index:200}}
.nav-dropdown.open .nav-dropdown-menu{{display:block}}
.nav-dropdown-item{{display:block;padding:8px 12px;border-radius:4px;font-size:13px;color:var(--text-secondary);cursor:pointer;transition:all .15s}}
.nav-dropdown-item:hover{{background:var(--bg-hover);color:var(--text-primary)}}
.nav-right{{margin-left:auto;display:flex;align-items:center;gap:12px}}
.nav-badge{{font-size:11px;padding:3px 10px;border-radius:12px;background:var(--blue-dim);color:#fff;font-weight:600}}
.nav-refresh{{font-size:11px;color:var(--text-muted)}}

/* ── LAYOUT ── */
.main-content{{flex:1;padding:24px;max-width:1440px;margin:0 auto;width:100%}}
.page{{display:none}}.page.active{{display:block}}
.page-header{{margin-bottom:20px}}
.page-title{{font-size:20px;font-weight:700;color:var(--text-primary)}}
.page-subtitle{{font-size:13px;color:var(--text-secondary);margin-top:4px}}

/* ── STATUS BANNER ── */
.status-banner{{padding:12px 16px;border-radius:8px;margin-bottom:20px;font-size:13px;font-weight:500;display:flex;align-items:center;gap:8px}}
.status-banner.ok{{background:rgba(34,197,94,.1);border:1px solid rgba(34,197,94,.3);color:var(--green)}}
.status-banner.warn{{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);color:var(--amber)}}
.status-banner.danger{{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:var(--red)}}
.status-dot{{width:8px;height:8px;border-radius:50%;flex-shrink:0}}
.status-banner.ok .status-dot{{background:var(--green)}}.status-banner.warn .status-dot{{background:var(--amber)}}.status-banner.danger .status-dot{{background:var(--red)}}

/* ── CARDS ── */
.card-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:20px}}
.card{{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;padding:16px}}
.card-label{{font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted);margin-bottom:6px;font-weight:600}}
.card-value{{font-size:24px;font-weight:700;color:var(--text-primary)}}
.card-sub{{font-size:12px;color:var(--text-secondary);margin-top:4px}}
.card-value.positive{{color:var(--green)}}.card-value.negative{{color:var(--red)}}

/* ── TABLES ── */
.table-section{{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;overflow:hidden;margin-bottom:20px}}
.table-header{{padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}}
.table-header h3{{font-size:14px;font-weight:600;color:var(--text-primary)}}
.table-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:10px 16px;font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted);font-weight:600;border-bottom:1px solid var(--border);white-space:nowrap}}
td{{padding:10px 16px;border-bottom:1px solid var(--border);font-size:13px;color:var(--text-primary);white-space:nowrap}}
tr:last-child td{{border-bottom:none}}
tr:hover td{{background:var(--bg-hover)}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}

/* ── PILLS & BADGES ── */
.pill{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}}
.pill-green{{background:rgba(34,197,94,.15);color:var(--green)}}.pill-red{{background:rgba(239,68,68,.15);color:var(--red)}}
.pill-amber{{background:rgba(245,158,11,.15);color:var(--amber)}}.pill-blue{{background:rgba(59,130,246,.15);color:var(--blue)}}
.pill-purple{{background:rgba(168,85,247,.15);color:var(--purple)}}
.leveraged-badge{{background:rgba(245,158,11,.2);color:var(--amber);font-size:10px;padding:1px 6px;border-radius:3px;font-weight:700;margin-left:6px}}

/* ── PROGRESS BARS ── */
.progress-wrap{{margin:8px 0}}
.progress-label{{display:flex;justify-content:space-between;font-size:12px;color:var(--text-secondary);margin-bottom:4px}}
.progress-bar{{height:6px;background:var(--bg-hover);border-radius:3px;overflow:hidden}}
.progress-fill{{height:100%;border-radius:3px;transition:width .3s}}

/* ── BUTTONS ── */
.btn{{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:6px;font-size:13px;font-weight:500;cursor:pointer;border:1px solid var(--border);background:var(--bg-card);color:var(--text-primary);transition:all .15s}}
.btn:hover{{background:var(--bg-hover);border-color:var(--border-light)}}
.btn-green{{background:var(--green-dim);border-color:var(--green-dim);color:#fff}}.btn-green:hover{{background:var(--green)}}
.btn-red{{background:var(--red-dim);border-color:var(--red-dim);color:#fff}}.btn-red:hover{{background:var(--red)}}
.btn-blue{{background:var(--blue-dim);border-color:var(--blue-dim);color:#fff}}.btn-blue:hover{{background:var(--blue)}}
.btn-group{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}}

/* ── LOG ── */
.log-container{{background:var(--bg-card);border:1px solid var(--border);border-radius:8px;max-height:300px;overflow-y:auto;padding:12px 16px;font-family:'SF Mono',Monaco,Consolas,monospace;font-size:12px;line-height:1.8}}
.log-entry{{color:var(--text-secondary)}}
.log-entry.buy{{color:var(--green)}}.log-entry.sell{{color:var(--red)}}.log-entry.reject{{color:var(--amber)}}.log-entry.info{{color:var(--blue)}}

/* ── SECTION ── */
.section-title{{font-size:15px;font-weight:600;color:var(--text-primary);margin:24px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--border)}}
.empty-state{{text-align:center;padding:40px 20px;color:var(--text-muted);font-size:13px}}

/* ── COPY BUTTON ── */
.copy-area{{background:var(--bg-secondary);border:1px solid var(--border);border-radius:8px;padding:16px;font-family:monospace;font-size:12px;white-space:pre-wrap;max-height:400px;overflow-y:auto;color:var(--text-secondary)}}
</style>
</head>
<body>

<!-- ══ NAV ══ -->
<nav class="top-nav">
  <div class="nav-brand">Strategy Factory<span>ETF Desk</span></div>
  <div class="nav-links">
    <div class="nav-link active" onclick="showPage('overview')">Overview</div>
    <div class="nav-link" onclick="showPage('alpaca-live')">Alpaca</div>
    <div class="nav-link" onclick="showPage('claude-analysis')">Claude Analysis</div>
    <div class="nav-dropdown" id="advDropdown">
      <div class="nav-link" onclick="toggleAdvanced(event)">Advanced ▾</div>
      <div class="nav-dropdown-menu">
        <div class="nav-dropdown-item" onclick="showPage('quantum')">Strategy Scorecard</div>
        <div class="nav-dropdown-item" onclick="showPage('bots')">Bot Signals</div>
        <div class="nav-dropdown-item" onclick="showPage('performance')">Performance</div>
        <div class="nav-dropdown-item" onclick="showPage('regime')">Market Regime</div>
        <div class="nav-dropdown-item" onclick="showPage('decisions')">Decision Log</div>
        <div class="nav-dropdown-item" onclick="showPage('portfolio')">Portfolio</div>
      </div>
    </div>
  </div>
  <div class="nav-right">
    <span class="nav-badge">10 ETFs Active</span>
    <span class="nav-refresh" id="lastRefresh">Updated: {timestamp}</span>
  </div>
</nav>

<div class="main-content">

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- PAGE: OVERVIEW                                                -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="page active" id="page-overview">
  <div id="overviewBanner" class="status-banner ok">
    <div class="status-dot"></div>
    <span id="bannerText">Loading system status...</span>
  </div>

  <div class="card-grid">
    <div class="card"><div class="card-label">Equity</div><div class="card-value" id="ovEquity">—</div><div class="card-sub" id="ovEquitySub">Loading...</div></div>
    <div class="card"><div class="card-label">Buying Power</div><div class="card-value" id="ovBuyingPower">—</div></div>
    <div class="card"><div class="card-label">Cash</div><div class="card-value" id="ovCash">—</div></div>
    <div class="card"><div class="card-label">Open Positions</div><div class="card-value" id="ovPositions">—</div><div class="card-sub">of 10 ETFs</div></div>
    <div class="card"><div class="card-label">Daily Mode</div><div class="card-value" id="ovMode">—</div><div class="card-sub" id="ovModeSub"></div></div>
  </div>

  <div class="section-title">Daily P&L Tracker</div>
  <div class="card-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
    <div class="card"><div class="card-label">Today Gross P&L</div><div class="card-value" id="ovGross">—</div></div>
    <div class="card"><div class="card-label">Est. Fees</div><div class="card-value" id="ovFees">—</div></div>
    <div class="card"><div class="card-label">Today Net P&L</div><div class="card-value" id="ovNet" style="font-size:28px">—</div></div>
    <div class="card">
      <div class="card-label">Profit Target (+1%)</div>
      <div class="card-value" id="ovTarget">—</div>
      <div class="progress-wrap"><div class="progress-label"><span>Progress</span><span id="ovTargetPct">0%</span></div><div class="progress-bar"><div class="progress-fill" id="ovTargetBar" style="width:0;background:var(--green)"></div></div></div>
    </div>
    <div class="card">
      <div class="card-label">Loss Limit (-0.5%)</div>
      <div class="card-value" id="ovLimit">—</div>
      <div class="progress-wrap"><div class="progress-label"><span>Exposure</span><span id="ovLimitPct">0%</span></div><div class="progress-bar"><div class="progress-fill" id="ovLimitBar" style="width:0;background:var(--red)"></div></div></div>
    </div>
  </div>

  <div class="section-title">ETF Performance</div>
  <div class="table-section">
    <div class="table-wrap">
      <table>
        <thead><tr><th>ETF</th><th class="num">Price</th><th class="num">Day %</th><th>Position</th><th>Signal</th><th class="num">Score</th><th class="num">Net P&L</th><th>Last Action</th></tr></thead>
        <tbody id="ovEtfBody"></tbody>
      </table>
    </div>
    <div id="ovEtfEmpty" class="empty-state">Loading ETF data...</div>
  </div>

  <div class="section-title">Top Opportunities</div>
  <div class="table-section">
    <div class="table-wrap">
      <table>
        <thead><tr><th>ETF</th><th>Strategy</th><th class="num">Score</th><th class="num">Confidence</th><th class="num">R:R</th><th>Reason</th><th>Status</th></tr></thead>
        <tbody id="ovOppsBody"></tbody>
      </table>
    </div>
    <div id="ovOppsEmpty" class="empty-state">No opportunities this cycle</div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- PAGE: ALPACA                                                  -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="page" id="page-alpaca-live">
  <div class="page-header"><div class="page-title">Alpaca Paper Trading</div><div class="page-subtitle">Live positions, orders, and controls</div></div>

  <div class="section-title">Controls</div>
  <div class="btn-group">
    <button class="btn btn-green" id="btnAutoStart" onclick="alpAutoStart()">Start Auto-Trader</button>
    <button class="btn btn-red" id="btnAutoStop" onclick="alpAutoStop()">Stop Auto-Trader</button>
    <button class="btn btn-blue" onclick="alpRunOnce()">Manual Scan</button>
    <button class="btn btn-red" onclick="alpKillAll()">Emergency Close All</button>
    <span class="pill pill-blue" id="alpAutoStatus">Loading...</span>
  </div>

  <div class="section-title">Open Positions</div>
  <div class="table-section">
    <div class="table-wrap">
      <table>
        <thead><tr><th>Symbol</th><th class="num">Qty</th><th class="num">Entry</th><th class="num">Current</th><th class="num">Gross P&L</th><th class="num">Est Fee</th><th class="num">Net P&L</th><th class="num">SL</th><th class="num">TP</th></tr></thead>
        <tbody id="alpPosBody"></tbody>
      </table>
    </div>
    <div id="alpPosEmpty" class="empty-state">No open positions</div>
  </div>

  <div class="section-title">Recent Orders</div>
  <div class="table-section">
    <div class="table-wrap">
      <table>
        <thead><tr><th>Time</th><th>Side</th><th>Symbol</th><th class="num">Qty</th><th class="num">Price</th><th>Status</th></tr></thead>
        <tbody id="alpOrdBody"></tbody>
      </table>
    </div>
    <div id="alpOrdEmpty" class="empty-state">No recent orders</div>
  </div>

  <div class="section-title">Live Log</div>
  <div class="log-container" id="alpLogContainer">
    <div class="log-entry info">System log loading...</div>
  </div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- PAGE: CLAUDE ANALYSIS                                         -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="page" id="page-claude-analysis">
  <div class="page-header"><div class="page-title">System Intelligence</div><div class="page-subtitle">Everything Claude needs to review and improve the system</div></div>

  <div class="section-title">Active Universe</div>
  <div class="table-section">
    <div class="table-wrap">
      <table>
        <thead><tr><th>ETF</th><th>Type</th><th>Status</th><th>Bots</th><th class="num">Trades</th><th class="num">Net P&L</th><th>Notes</th></tr></thead>
        <tbody id="clUniverseBody"></tbody>
      </table>
    </div>
  </div>

  <div class="section-title">Strategy Performance</div>
  <div class="table-section">
    <div class="table-wrap">
      <table>
        <thead><tr><th>Strategy</th><th class="num">Trades</th><th class="num">Win %</th><th class="num">Net P&L</th><th class="num">PF</th><th class="num">Avg Win</th><th class="num">Avg Loss</th><th class="num">Max DD</th><th class="num">Lose Streak</th><th>Best ETF</th><th>Action</th></tr></thead>
        <tbody id="clStratBody"></tbody>
      </table>
    </div>
    <div id="clStratEmpty" class="empty-state">No closed trades yet</div>
  </div>

  <div class="section-title">Daily Decision Summary</div>
  <div class="card-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
    <div class="card"><div class="card-label">CEO Regime</div><div class="card-value" id="clRegime" style="font-size:16px">—</div></div>
    <div class="card"><div class="card-label">CEO Direction</div><div class="card-value" id="clDirection" style="font-size:16px">—</div></div>
    <div class="card"><div class="card-label">ETFs Scanned</div><div class="card-value" id="clScanned">—</div></div>
    <div class="card"><div class="card-label">Signals Found</div><div class="card-value" id="clSignals">—</div></div>
    <div class="card"><div class="card-label">Trades Accepted</div><div class="card-value" id="clAccepted">—</div></div>
    <div class="card"><div class="card-label">Trades Rejected</div><div class="card-value" id="clRejected">—</div></div>
  </div>

  <div class="section-title">Risk Summary</div>
  <div class="card-grid" style="grid-template-columns:repeat(auto-fit,minmax(180px,1fr))">
    <div class="card"><div class="card-label">Daily Mode</div><div class="card-value" id="clRiskMode" style="font-size:16px">—</div></div>
    <div class="card"><div class="card-label">Daily Net P&L</div><div class="card-value" id="clRiskPL">—</div></div>
    <div class="card"><div class="card-label">Open Risk Used</div><div class="card-value" id="clRiskUsed">—</div></div>
    <div class="card"><div class="card-label">Quality Threshold</div><div class="card-value" id="clRiskThresh">—</div></div>
  </div>

  <div class="section-title">Fee / Net P&L Summary</div>
  <div class="card-grid" style="grid-template-columns:repeat(3,1fr)">
    <div class="card"><div class="card-label">Gross P&L (All Time)</div><div class="card-value" id="clFeeGross">—</div></div>
    <div class="card"><div class="card-label">Total Fees</div><div class="card-value" id="clFeeFees">—</div></div>
    <div class="card"><div class="card-label">Net P&L (All Time)</div><div class="card-value" id="clFeeNet">—</div></div>
  </div>

  <div class="section-title">Claude-Ready Export</div>
  <button class="btn btn-blue" onclick="copyClaudeExport()" style="margin-bottom:12px">Copy System Summary for Claude</button>
  <div class="copy-area" id="clExportArea">Click "Copy System Summary" to generate...</div>
</div>

<!-- ══════════════════════════════════════════════════════════════ -->
<!-- ADVANCED PAGES (legacy — kept for reference)                  -->
<!-- ══════════════════════════════════════════════════════════════ -->
<div class="page" id="page-quantum"><div class="page-header"><div class="page-title">Strategy Scorecard (Advanced)</div></div>
  <div class="table-section"><div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="num">Trades</th><th class="num">Net P&L</th><th class="num">Win %</th><th class="num">PF</th><th>Action</th></tr></thead><tbody id="advScoreBody"></tbody></table></div>
  <div id="advScoreEmpty" class="empty-state">No data yet</div></div>
</div>
<div class="page" id="page-bots"><div class="page-header"><div class="page-title">Bot Signals (Advanced)</div></div>
  <div class="table-section"><div class="table-wrap"><table><thead><tr><th>Symbol</th><th>Action</th><th>Strategy</th><th class="num">Confidence</th><th class="num">Score</th><th>Reason</th></tr></thead><tbody id="advBotsBody"></tbody></table></div>
  <div id="advBotsEmpty" class="empty-state">No signals</div></div>
</div>
<div class="page" id="page-performance"><div class="page-header"><div class="page-title">Performance (Advanced)</div></div>
  <div class="table-section"><div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="num">Trades</th><th class="num">Net P&L</th><th class="num">Win %</th><th class="num">Fees</th><th>Action</th></tr></thead><tbody id="advPerfBody"></tbody></table></div>
  <div id="advPerfEmpty" class="empty-state">No data</div></div>
</div>
<div class="page" id="page-regime"><div class="page-header"><div class="page-title">Market Regime (Advanced)</div></div>
  <div class="card-grid"><div class="card"><div class="card-label">Current Regime</div><div class="card-value" id="advRegime">—</div></div>
  <div class="card"><div class="card-label">Direction</div><div class="card-value" id="advDirection">—</div></div>
  <div class="card"><div class="card-label">Posture</div><div class="card-value" id="advPosture">—</div></div></div>
</div>
<div class="page" id="page-decisions"><div class="page-header"><div class="page-title">Decision Log (Advanced)</div></div>
  <div class="log-container" id="advDecLog"><div class="log-entry info">No decisions logged yet</div></div>
</div>
<div class="page" id="page-portfolio"><div class="page-header"><div class="page-title">Portfolio (Advanced)</div></div>
  <div class="empty-state">Portfolio view — see Claude Analysis for current data.</div>
</div>
<div class="page" id="page-learning"><div class="page-header"><div class="page-title">Learning Engine (Advanced)</div></div>
  <div class="empty-state">Learning engine data — see Claude Analysis for strategy performance.</div>
</div>

</div><!-- /main-content -->

<script>
/* ══ NAVIGATION ══ */
function showPage(id) {{
  document.querySelectorAll('.page').forEach(function(p) {{ p.classList.remove('active'); }});
  document.querySelectorAll('.nav-link').forEach(function(n) {{ n.classList.remove('active'); }});
  var el = document.getElementById('page-' + id);
  if (el) el.classList.add('active');
  // Highlight nav
  var links = document.querySelectorAll('.nav-links > .nav-link, .nav-links > .nav-dropdown > .nav-link');
  links.forEach(function(l) {{
    if (l.textContent.trim().replace(' ▾','') === getPageLabel(id)) l.classList.add('active');
  }});
  document.getElementById('advDropdown').classList.remove('open');
  refreshData(id);
}}
function getPageLabel(id) {{
  var map = {{'overview':'Overview','alpaca-live':'Alpaca','claude-analysis':'Claude Analysis'}};
  return map[id] || 'Advanced';
}}
function toggleAdvanced(e) {{
  e.stopPropagation();
  document.getElementById('advDropdown').classList.toggle('open');
}}
document.addEventListener('click', function() {{ document.getElementById('advDropdown').classList.remove('open'); }});

/* ══ HELPERS ══ */
function $(id) {{ return document.getElementById(id); }}
function escHtml(s) {{ var d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }}
function signedMoney(v) {{ v = Number(v || 0); return (v >= 0 ? '+' : '') + '$' + Math.abs(v).toFixed(2); }}
function pct(v) {{ return Number(v || 0).toFixed(1) + '%'; }}
function plColor(v) {{ return Number(v || 0) >= 0 ? 'var(--green)' : 'var(--red)'; }}
function plClass(v) {{ return Number(v || 0) >= 0 ? 'positive' : 'negative'; }}
var LEVERAGED = {{'SOXL':1,'TQQQ':1,'SOXS':1}};
function leveragedBadge(sym) {{ return LEVERAGED[sym] ? '<span class="leveraged-badge">LEV</span>' : ''; }}

/* ══ DATA CACHE ══ */
var _cache = {{}};
var _loading = {{}};

async function fetchJSON(url) {{
  try {{
    var r = await fetch(url);
    if (!r.ok) return null;
    return await r.json();
  }} catch(e) {{ return null; }}
}}

async function loadInsightData(force) {{
  if (!force && _cache.insight) return _cache.insight;
  if (_loading.insight) return _cache.insight || {{}};
  _loading.insight = true;
  try {{
    var d = await fetchJSON('/api/insight-data');
    if (d) _cache.insight = d;
  }} catch(e) {{}}
  _loading.insight = false;
  return _cache.insight || {{}};
}}

async function loadAlpacaData(force) {{
  if (!force && _cache.alpaca) return _cache.alpaca;
  _loading.alpaca = true;
  try {{
    var [acct, pos, orders, ledger, status, conserv] = await Promise.all([
      fetchJSON('/api/alpaca/account'),
      fetchJSON('/api/alpaca/positions'),
      fetchJSON('/api/alpaca/orders'),
      fetchJSON('/api/alpaca/trade-ledger'),
      fetchJSON('/api/alpaca/auto/status'),
      fetchJSON('/api/alpaca/conservative-status'),
    ]);
    _cache.alpaca = {{account: acct, positions: pos, orders: orders, ledger: ledger, status: status, conservative: conserv}};
  }} catch(e) {{}}
  _loading.alpaca = false;
  return _cache.alpaca || {{}};
}}

/* ══ REFRESH ══ */
async function refreshData(pageId) {{
  var alp = await loadAlpacaData(true);
  var insight = await loadInsightData(true);
  $('lastRefresh').textContent = 'Updated: ' + new Date().toLocaleTimeString();

  if (pageId === 'overview' || !pageId) renderOverview(alp, insight);
  if (pageId === 'alpaca-live') renderAlpaca(alp);
  if (pageId === 'claude-analysis') renderClaude(alp, insight);
  if (pageId === 'quantum' || pageId === 'performance') renderAdvScorecard(alp);
  if (pageId === 'bots') renderAdvBots(insight);
  if (pageId === 'regime') renderAdvRegime(insight);
}}

/* ══ RENDER: OVERVIEW ══ */
function renderOverview(alp, insight) {{
  var acct = (alp && alp.account) || {{}};
  var pos = (alp && alp.positions) || [];
  var conserv = (alp && alp.conservative) || {{}};
  var desk = (insight && insight.desk) || (insight && insight.state) || {{}};
  var ceo = (desk && desk.ceo) || {{}};

  // Account cards
  var equity = Number(acct.equity || acct.portfolio_value || 0);
  $('ovEquity').textContent = '$' + equity.toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
  $('ovEquitySub').textContent = acct.status ? 'Status: ' + acct.status : '';
  $('ovBuyingPower').textContent = '$' + Number(acct.buying_power || acct.cash || 0).toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
  $('ovCash').textContent = '$' + Number(acct.cash || 0).toLocaleString(undefined, {{minimumFractionDigits:2, maximumFractionDigits:2}});
  $('ovPositions').textContent = (Array.isArray(pos) ? pos : []).length;

  // Daily P&L
  var mode = conserv.daily_mode || conserv.mode || 'SAFE_TEST_MODE';
  var modeName = mode.replace(/_/g, ' ').replace('MODE', '').trim();
  $('ovMode').textContent = modeName;
  $('ovMode').className = 'card-value' + (mode.includes('LOSS') ? ' negative' : mode.includes('PROFIT') ? ' positive' : '');
  var modeDesc = '';
  if (mode.includes('PROFIT')) modeDesc = 'Only 90+ score trades allowed';
  else if (mode.includes('LOSS')) modeDesc = 'Only 90+ score trades allowed';
  else modeDesc = 'Score 75+ trades allowed';
  $('ovModeSub').textContent = modeDesc;

  var realized = Number(conserv.realized_pl || 0);
  var unrealized = Number(conserv.unrealized_pl || 0);
  var fees = Number(conserv.total_fees_today || conserv.estimated_fees || 0);
  var grossPL = realized + unrealized;
  var netPL = realized + unrealized - fees;

  $('ovGross').textContent = signedMoney(grossPL);
  $('ovGross').className = 'card-value ' + plClass(grossPL);
  $('ovFees').textContent = '-$' + Math.abs(fees).toFixed(2);
  $('ovNet').textContent = signedMoney(netPL);
  $('ovNet').className = 'card-value ' + plClass(netPL);

  var profitTarget = equity * 0.01;
  var lossLimit = equity * 0.005;
  $('ovTarget').textContent = '+$' + profitTarget.toFixed(0);
  $('ovLimit').textContent = '-$' + lossLimit.toFixed(0);

  var targetPct = Math.min(100, Math.max(0, netPL / profitTarget * 100));
  $('ovTargetPct').textContent = (targetPct > 0 ? targetPct.toFixed(0) + '%' : '0%');
  $('ovTargetBar').style.width = Math.max(0, targetPct) + '%';

  var limitPct = Math.min(100, Math.max(0, Math.abs(Math.min(0, netPL)) / lossLimit * 100));
  $('ovLimitPct').textContent = limitPct.toFixed(0) + '%';
  $('ovLimitBar').style.width = limitPct + '%';

  // Banner
  var banner = $('overviewBanner');
  if (mode.includes('LOSS')) {{
    banner.className = 'status-banner danger';
    $('bannerText').textContent = 'Loss Protection Mode active. Only 90+ score trades allowed.';
  }} else if (mode.includes('PROFIT')) {{
    banner.className = 'status-banner warn';
    $('bannerText').textContent = 'Profit Protection Mode active. Only 90+ score trades allowed.';
  }} else {{
    var posCount = (Array.isArray(pos) ? pos : []).length;
    banner.className = 'status-banner ok';
    $('bannerText').textContent = 'System scanning normally. ' + posCount + ' position' + (posCount !== 1 ? 's' : '') + ' open.';
  }}

  // ETF table
  var managers = (desk && desk.managers) || [];
  var symbols = Object.values((desk && desk.symbols) || {{}});
  var etfBody = $('ovEtfBody');
  var etfEmpty = $('ovEtfEmpty');
  if (managers.length || symbols.length) {{
    etfEmpty.style.display = 'none';
    var etfRows = (managers.length ? managers : symbols).map(function(m) {{
      var sym = m.symbol || m.sym || '?';
      var action = m.action || (m.accepted ? 'BUY' : 'SKIP');
      var actionCls = action === 'enter' || action === 'BUY' ? 'pill-green' : (action === 'cooldown' ? 'pill-red' : 'pill-amber');
      var score = Number(m.score || 0);
      var conf = Number(m.confidence || 0);
      var reason = m.reason || m.rejection_reason || '';
      // Find position P&L
      var posPL = 0;
      (Array.isArray(pos) ? pos : []).forEach(function(p) {{ if (p.symbol === sym) posPL = Number(p.unrealized_pl || 0); }});
      var posStatus = (Array.isArray(pos) ? pos : []).some(function(p) {{ return p.symbol === sym; }}) ? '<span class="pill pill-green">OPEN</span>' : '<span class="pill pill-amber">—</span>';
      return '<tr><td><strong>' + escHtml(sym) + '</strong>' + leveragedBadge(sym) + '</td>' +
        '<td class="num">—</td><td class="num">—</td>' +
        '<td>' + posStatus + '</td>' +
        '<td><span class="pill ' + actionCls + '">' + escHtml(String(action).toUpperCase()) + '</span></td>' +
        '<td class="num">' + (score > 0 ? score.toFixed(0) : '—') + '</td>' +
        '<td class="num" style="color:' + plColor(posPL) + '">' + (posPL ? signedMoney(posPL) : '—') + '</td>' +
        '<td style="max-width:300px;white-space:normal;font-size:12px;color:var(--text-secondary)">' + escHtml(reason.substring(0, 200)) + '</td></tr>';
    }});
    etfBody.innerHTML = etfRows.join('');
  }} else {{
    etfEmpty.style.display = 'block';
    etfBody.innerHTML = '';
  }}

  // Opportunities
  var opps = managers.filter(function(m) {{ return m.action === 'enter' || Number(m.confidence || 0) >= 0.5; }});
  opps.sort(function(a, b) {{ return Number(b.score || 0) - Number(a.score || 0); }});
  var oppsBody = $('ovOppsBody');
  var oppsEmpty = $('ovOppsEmpty');
  if (opps.length) {{
    oppsEmpty.style.display = 'none';
    oppsBody.innerHTML = opps.slice(0, 10).map(function(m) {{
      var status = m.action === 'enter' ? '<span class="pill pill-green">ACCEPTED</span>' : '<span class="pill pill-amber">REJECTED</span>';
      return '<tr><td><strong>' + escHtml(m.symbol || '?') + '</strong>' + leveragedBadge(m.symbol || '') + '</td>' +
        '<td>' + escHtml(m.active_strategy || '') + '</td>' +
        '<td class="num">' + Number(m.score || 0).toFixed(0) + '</td>' +
        '<td class="num">' + Number(m.confidence || 0).toFixed(2) + '</td>' +
        '<td class="num">—</td>' +
        '<td style="max-width:250px;white-space:normal;font-size:12px">' + escHtml((m.reason || '').substring(0, 150)) + '</td>' +
        '<td>' + status + '</td></tr>';
    }}).join('');
  }} else {{
    oppsEmpty.style.display = 'block';
    oppsBody.innerHTML = '';
  }}
}}

/* ══ RENDER: ALPACA ══ */
function renderAlpaca(alp) {{
  var pos = (alp && alp.positions) || [];
  var orders = (alp && alp.orders) || [];
  var status = (alp && alp.status) || {{}};
  var insight = _cache.insight || {{}};
  var desk = insight.desk || insight.state || {{}};

  // Auto-trader status
  var autoEl = $('alpAutoStatus');
  if (status.enabled) {{
    autoEl.textContent = 'AUTO-TRADER ON';
    autoEl.className = 'pill pill-green';
  }} else {{
    autoEl.textContent = 'AUTO-TRADER OFF';
    autoEl.className = 'pill pill-red';
  }}

  // Positions
  var posBody = $('alpPosBody');
  var posEmpty = $('alpPosEmpty');
  if (Array.isArray(pos) && pos.length) {{
    posEmpty.style.display = 'none';
    posBody.innerHTML = pos.map(function(p) {{
      var sym = p.symbol || '?';
      var qty = Number(p.qty || p.quantity || 0);
      var entry = Number(p.avg_entry_price || 0);
      var current = Number(p.current_price || 0);
      var grossPL = Number(p.unrealized_pl || 0);
      var estFee = Math.abs(entry * qty * 0.0001) + Math.abs(current * qty * 0.0001);
      var netPL = grossPL - estFee;
      return '<tr><td><strong>' + escHtml(sym) + '</strong>' + leveragedBadge(sym) + '</td>' +
        '<td class="num">' + qty + '</td>' +
        '<td class="num">$' + entry.toFixed(2) + '</td>' +
        '<td class="num">$' + current.toFixed(2) + '</td>' +
        '<td class="num" style="color:' + plColor(grossPL) + '">' + signedMoney(grossPL) + '</td>' +
        '<td class="num">-$' + estFee.toFixed(2) + '</td>' +
        '<td class="num" style="color:' + plColor(netPL) + ';font-weight:700">' + signedMoney(netPL) + '</td>' +
        '<td class="num">—</td><td class="num">—</td></tr>';
    }}).join('');
  }} else {{
    posEmpty.style.display = 'block';
    posBody.innerHTML = '';
  }}

  // Orders
  var ordBody = $('alpOrdBody');
  var ordEmpty = $('alpOrdEmpty');
  var ordList = Array.isArray(orders) ? orders : (orders && orders.orders ? orders.orders : []);
  if (ordList.length) {{
    ordEmpty.style.display = 'none';
    ordBody.innerHTML = ordList.slice(0, 20).map(function(o) {{
      var side = (o.side || 'buy').toUpperCase();
      var sideCls = side === 'BUY' ? 'pill-green' : 'pill-red';
      var time = (o.submitted_at || o.created_at || '').replace('T', ' ').substring(0, 19);
      return '<tr><td>' + escHtml(time) + '</td>' +
        '<td><span class="pill ' + sideCls + '">' + side + '</span></td>' +
        '<td><strong>' + escHtml(o.symbol || '?') + '</strong></td>' +
        '<td class="num">' + (o.filled_qty || o.qty || '—') + '</td>' +
        '<td class="num">' + (o.filled_avg_price ? '$' + Number(o.filled_avg_price).toFixed(2) : '—') + '</td>' +
        '<td><span class="pill pill-blue">' + escHtml((o.status || '').toUpperCase()) + '</span></td></tr>';
    }}).join('');
  }} else {{
    ordEmpty.style.display = 'block';
    ordBody.innerHTML = '';
  }}

  // Log
  var logContainer = $('alpLogContainer');
  var managers = (desk && desk.managers) || [];
  if (managers.length) {{
    logContainer.innerHTML = managers.map(function(m) {{
      var sym = m.symbol || '?';
      var action = m.action || 'wait';
      var cls = action === 'enter' ? 'buy' : (action === 'cooldown' ? 'reject' : 'info');
      var score = Number(m.score || 0);
      var reason = m.reason || m.rejection_reason || 'No reason';
      if (action === 'enter') {{
        return '<div class="log-entry buy">' + sym + ' accepted: score ' + score.toFixed(0) + ', ' + escHtml(reason.substring(0, 150)) + '</div>';
      }} else {{
        return '<div class="log-entry reject">' + sym + ' rejected: score ' + score.toFixed(0) + ', ' + escHtml(reason.substring(0, 150)) + '</div>';
      }}
    }}).join('');
  }}
}}

/* ══ RENDER: CLAUDE ANALYSIS ══ */
function renderClaude(alp, insight) {{
  var desk = (insight && insight.desk) || (insight && insight.state) || {{}};
  var ceo = (desk && desk.ceo) || {{}};
  var managers = (desk && desk.managers) || [];
  var conserv = (alp && alp.conservative) || {{}};
  var ledger = (alp && alp.ledger) || {{}};
  var rows = (ledger && ledger.rows) || [];

  // CEO
  $('clRegime').textContent = (ceo.market_regime || '—').replace(/_/g, ' ').toUpperCase();
  $('clDirection').textContent = (ceo.market_direction || '—').toUpperCase();

  // Scan stats
  $('clScanned').textContent = managers.length || '0';
  var accepted = managers.filter(function(m) {{ return m.action === 'enter'; }}).length;
  $('clSignals').textContent = managers.filter(function(m) {{ return Number(m.confidence || 0) > 0.4; }}).length;
  $('clAccepted').textContent = accepted;
  $('clRejected').textContent = (managers.length - accepted);

  // Risk
  var mode = conserv.daily_mode || conserv.mode || 'SAFE_TEST_MODE';
  $('clRiskMode').textContent = mode.replace(/_/g, ' ').replace('MODE', '').trim();
  var realized = Number(conserv.realized_pl || 0);
  var unrealized = Number(conserv.unrealized_pl || 0);
  $('clRiskPL').textContent = signedMoney(realized + unrealized);
  $('clRiskPL').className = 'card-value ' + plClass(realized + unrealized);
  $('clRiskUsed').textContent = '$' + Number(conserv.open_risk_used || 0).toFixed(2);
  $('clRiskThresh').textContent = String(conserv.quality_threshold || conserv.required_quality_score || 75);

  // Universe table
  var ETFS = ['QQQ','SPY','SOXL','IWM','TQQQ','SOXX','SMH','RSP','SOXS','VOO'];
  var etfTypes = {{'QQQ':'Nasdaq 100','SPY':'S&P 500','SOXL':'Semi 3x Bull','IWM':'Russell 2000','TQQQ':'Nasdaq 3x Bull','SOXX':'Semiconductors','SMH':'Semi (VanEck)','RSP':'S&P Equal Weight','SOXS':'Semi 3x Bear','VOO':'S&P 500 (Vanguard)'}};
  var uniBody = $('clUniverseBody');
  // Count trades per ETF from ledger
  var etfTrades = {{}};
  var etfPL = {{}};
  rows.forEach(function(r) {{
    var s = r.symbol || '';
    etfTrades[s] = (etfTrades[s] || 0) + 1;
    etfPL[s] = (etfPL[s] || 0) + Number(r.net_pl || 0);
  }});
  uniBody.innerHTML = ETFS.map(function(etf) {{
    var t = etfTrades[etf] || 0;
    var pl = etfPL[etf] || 0;
    var type = etfTypes[etf] || '';
    var isLev = LEVERAGED[etf];
    var notes = isLev ? '50% position size, tighter risk' : '';
    return '<tr><td><strong>' + etf + '</strong>' + leveragedBadge(etf) + '</td>' +
      '<td>' + type + '</td>' +
      '<td><span class="pill pill-green">Active</span></td>' +
      '<td>10</td>' +
      '<td class="num">' + t + '</td>' +
      '<td class="num" style="color:' + plColor(pl) + '">' + signedMoney(pl) + '</td>' +
      '<td style="font-size:12px;color:var(--text-secondary)">' + notes + '</td></tr>';
  }}).join('');

  // Strategy performance from ledger
  var stratBody = $('clStratBody');
  var stratEmpty = $('clStratEmpty');
  if (rows.length) {{
    stratEmpty.style.display = 'none';
    var strats = {{}};
    rows.forEach(function(r) {{
      var s = r.strategy || 'unknown';
      if (!strats[s]) strats[s] = {{trades:0, wins:0, losses:0, net:0, grossWins:0, grossLosses:0, maxDD:0, runPL:0, peakPL:0, curLose:0, maxLose:0, assets:{{}}}};
      var g = strats[s];
      var net = Number(r.net_pl || 0);
      g.trades++; g.net += net;
      if (net > 0) {{ g.wins++; g.grossWins += net; g.curLose = 0; }}
      if (net < 0) {{ g.losses++; g.grossLosses += Math.abs(net); g.curLose++; if (g.curLose > g.maxLose) g.maxLose = g.curLose; }}
      g.runPL += net; if (g.runPL > g.peakPL) g.peakPL = g.runPL;
      var dd = g.peakPL - g.runPL; if (dd > g.maxDD) g.maxDD = dd;
      var sym = r.symbol || '?';
      g.assets[sym] = (g.assets[sym] || 0) + net;
    }});
    var stratList = Object.entries(strats).sort(function(a,b) {{ return b[1].net - a[1].net; }});
    stratBody.innerHTML = stratList.map(function(kv) {{
      var s = kv[0], g = kv[1];
      var wr = g.trades ? g.wins / g.trades * 100 : 0;
      var pf = g.grossLosses > 0 ? g.grossWins / g.grossLosses : (g.grossWins > 0 ? 99 : 0);
      var avgWin = g.wins ? g.grossWins / g.wins : 0;
      var avgLoss = g.losses ? g.grossLosses / g.losses : 0;
      var assetArr = Object.entries(g.assets).sort(function(a,b) {{ return b[1] - a[1]; }});
      var bestAsset = assetArr.length ? assetArr[0][0] + ' ' + signedMoney(assetArr[0][1]) : '—';
      var pfColor = pf >= 1.5 ? 'var(--green)' : (pf >= 1.0 ? 'var(--amber)' : 'var(--red)');
      var action = g.trades < 3 ? '<span class="pill pill-amber">Collect</span>' :
                   (g.net > 0 && wr >= 50 ? '<span class="pill pill-green">Keep</span>' :
                   (g.net < 0 ? '<span class="pill pill-red">Tune</span>' : '<span class="pill pill-amber">Monitor</span>'));
      return '<tr><td><strong>' + escHtml(s) + '</strong></td>' +
        '<td class="num">' + g.trades + '</td>' +
        '<td class="num">' + pct(wr) + '</td>' +
        '<td class="num" style="color:' + plColor(g.net) + ';font-weight:700">' + signedMoney(g.net) + '</td>' +
        '<td class="num" style="color:' + pfColor + '">' + pf.toFixed(2) + '</td>' +
        '<td class="num" style="color:var(--green)">' + signedMoney(avgWin) + '</td>' +
        '<td class="num" style="color:var(--red)">' + signedMoney(-avgLoss) + '</td>' +
        '<td class="num" style="color:var(--red)">' + signedMoney(-g.maxDD) + '</td>' +
        '<td class="num">' + g.maxLose + '</td>' +
        '<td>' + bestAsset + '</td>' +
        '<td>' + action + '</td></tr>';
    }}).join('');
  }} else {{
    stratEmpty.style.display = 'block';
    stratBody.innerHTML = '';
  }}

  // Fee summary
  var totalGross = 0, totalFees = 0, totalNet = 0;
  rows.forEach(function(r) {{
    totalGross += Number(r.gross_pl || r.net_pl || 0);
    totalFees += Number(r.total_fees || 0);
    totalNet += Number(r.net_pl || 0);
  }});
  $('clFeeGross').textContent = signedMoney(totalGross);
  $('clFeeGross').className = 'card-value ' + plClass(totalGross);
  $('clFeeFees').textContent = '-$' + Math.abs(totalFees).toFixed(2);
  $('clFeeNet').textContent = signedMoney(totalNet);
  $('clFeeNet').className = 'card-value ' + plClass(totalNet);
}}

/* ══ CLAUDE EXPORT ══ */
function copyClaudeExport() {{
  var alp = _cache.alpaca || {{}};
  var insight = _cache.insight || {{}};
  var acct = alp.account || {{}};
  var conserv = alp.conservative || {{}};
  var desk = insight.desk || insight.state || {{}};
  var ceo = (desk && desk.ceo) || {{}};
  var managers = (desk && desk.managers) || [];
  var rows = ((alp.ledger || {{}}).rows) || [];

  var lines = [];
  lines.push('=== STRATEGY FACTORY SYSTEM STATUS ===');
  lines.push('Generated: ' + new Date().toISOString());
  lines.push('');
  lines.push('ACCOUNT: Equity $' + Number(acct.equity || 0).toFixed(2) + ' | Cash $' + Number(acct.cash || 0).toFixed(2));
  lines.push('UNIVERSE: 10 ETFs (QQQ, SPY, SOXL, IWM, TQQQ, SOXX, SMH, RSP, SOXS, VOO)');
  lines.push('LEVERAGED: SOXL, TQQQ, SOXS (50% position size)');
  lines.push('');
  lines.push('CEO REGIME: ' + (ceo.market_regime || 'unknown'));
  lines.push('CEO DIRECTION: ' + (ceo.market_direction || 'unknown'));
  lines.push('DAILY MODE: ' + (conserv.daily_mode || conserv.mode || 'unknown'));
  lines.push('');
  lines.push('DAILY P&L: Net ' + signedMoney(Number(conserv.realized_pl || 0) + Number(conserv.unrealized_pl || 0)));
  lines.push('OPEN POSITIONS: ' + ((alp.positions || []).length));
  lines.push('');
  lines.push('--- STRATEGY PERFORMANCE (ALL TIME) ---');
  var strats = {{}};
  rows.forEach(function(r) {{
    var s = r.strategy || 'unknown';
    if (!strats[s]) strats[s] = {{trades:0, wins:0, net:0}};
    strats[s].trades++; strats[s].net += Number(r.net_pl || 0);
    if (Number(r.net_pl || 0) > 0) strats[s].wins++;
  }});
  Object.entries(strats).sort(function(a,b) {{ return b[1].net - a[1].net; }}).forEach(function(kv) {{
    var s = kv[0], g = kv[1];
    var wr = g.trades ? (g.wins / g.trades * 100).toFixed(0) : '0';
    lines.push(s + ': ' + g.trades + ' trades, ' + wr + '% WR, net ' + signedMoney(g.net));
  }});
  lines.push('');
  lines.push('--- RECENT REJECTIONS ---');
  managers.filter(function(m) {{ return m.action !== 'enter'; }}).slice(0, 10).forEach(function(m) {{
    lines.push(m.symbol + ': ' + (m.reason || m.rejection_reason || 'no reason').substring(0, 120));
  }});
  lines.push('');
  lines.push('--- TOTAL TRADES: ' + rows.length + ' ---');
  var totalNet = 0; rows.forEach(function(r) {{ totalNet += Number(r.net_pl || 0); }});
  lines.push('TOTAL NET P&L: ' + signedMoney(totalNet));

  var text = lines.join('\\n');
  $('clExportArea').textContent = text;
  try {{
    navigator.clipboard.writeText(text);
  }} catch(e) {{}}
}}

/* ══ ADVANCED PAGES ══ */
function renderAdvScorecard(alp) {{
  var rows = ((alp && alp.ledger || {{}}).rows) || [];
  var body = $('advScoreBody') || $('advPerfBody');
  var empty = $('advScoreEmpty') || $('advPerfEmpty');
  if (!body) return;
  if (!rows.length) {{ if (empty) empty.style.display = 'block'; body.innerHTML = ''; return; }}
  if (empty) empty.style.display = 'none';
  var groups = {{}};
  rows.forEach(function(r) {{
    var k = r.strategy || 'unknown';
    if (!groups[k]) groups[k] = {{trades:0, wins:0, net:0}};
    groups[k].trades++; groups[k].net += Number(r.net_pl || 0);
    if (Number(r.net_pl || 0) > 0) groups[k].wins++;
  }});
  var list = Object.entries(groups).sort(function(a,b) {{ return b[1].net - a[1].net; }});
  body.innerHTML = list.map(function(kv) {{
    var s = kv[0], g = kv[1];
    var wr = g.trades ? g.wins / g.trades * 100 : 0;
    var pf = g.net > 0 ? '1.0+' : '<1.0';
    var action = g.net > 0 ? '<span class="pill pill-green">Keep</span>' : '<span class="pill pill-red">Tune</span>';
    return '<tr><td><strong>' + escHtml(s) + '</strong></td>' +
      '<td class="num">' + g.trades + '</td><td class="num" style="color:' + plColor(g.net) + '">' + signedMoney(g.net) + '</td>' +
      '<td class="num">' + pct(wr) + '</td><td class="num">' + pf + '</td><td>' + action + '</td></tr>';
  }}).join('');
}}
function renderAdvBots(insight) {{
  var desk = (insight && insight.desk) || (insight && insight.state) || {{}};
  var managers = (desk && desk.managers) || [];
  var body = $('advBotsBody');
  var empty = $('advBotsEmpty');
  if (!body) return;
  if (!managers.length) {{ if (empty) empty.style.display = 'block'; body.innerHTML = ''; return; }}
  if (empty) empty.style.display = 'none';
  body.innerHTML = managers.map(function(m) {{
    var cls = m.action === 'enter' ? 'pill-green' : 'pill-amber';
    return '<tr><td><strong>' + escHtml(m.symbol || '?') + '</strong></td>' +
      '<td><span class="pill ' + cls + '">' + escHtml(String(m.action || 'wait').toUpperCase()) + '</span></td>' +
      '<td>' + escHtml(m.active_strategy || '') + '</td>' +
      '<td class="num">' + Number(m.confidence || 0).toFixed(2) + '</td>' +
      '<td class="num">' + Number(m.score || 0).toFixed(0) + '</td>' +
      '<td style="max-width:300px;white-space:normal;font-size:12px">' + escHtml((m.reason || '').substring(0, 200)) + '</td></tr>';
  }}).join('');
}}
function renderAdvRegime(insight) {{
  var desk = (insight && insight.desk) || (insight && insight.state) || {{}};
  var ceo = (desk && desk.ceo) || {{}};
  $('advRegime').textContent = (ceo.market_regime || '—').replace(/_/g, ' ').toUpperCase();
  $('advDirection').textContent = (ceo.market_direction || '—').toUpperCase();
  $('advPosture').textContent = (ceo.posture || '—').toUpperCase();
}}

/* ══ ALPACA CONTROLS ══ */
async function alpAutoStart() {{ await fetch('/api/alpaca/auto/start', {{method:'POST'}}); setTimeout(function() {{ refreshData('alpaca-live'); }}, 1000); }}
async function alpAutoStop() {{ await fetch('/api/alpaca/auto/stop', {{method:'POST'}}); setTimeout(function() {{ refreshData('alpaca-live'); }}, 1000); }}
async function alpRunOnce() {{ await fetch('/api/alpaca/auto/run-once', {{method:'POST'}}); setTimeout(function() {{ refreshData('alpaca-live'); }}, 3000); }}
async function alpKillAll() {{
  if (!confirm('EMERGENCY: Close ALL positions? This cannot be undone.')) return;
  await fetch('/api/alpaca/close-all', {{method:'POST'}});
  setTimeout(function() {{ refreshData('alpaca-live'); }}, 2000);
}}

/* ══ AUTO REFRESH ══ */
setInterval(function() {{
  var activePage = document.querySelector('.page.active');
  if (activePage) {{
    var id = activePage.id.replace('page-', '');
    refreshData(id);
  }}
}}, 60000);

// Initial load
refreshData('overview');
</script>
</body>
</html>"""


    def save(self, html, path=None):
        """Write HTML to disk and return the output path."""
        out = path or DASHBOARD_OUTPUT
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        return out


# ── Standalone mock data for testing ──────────────────────────────────
def generate_mock_data(n=10):
    """Generate mock data for preview/testing."""
    import random
    names = [f"Bot-{i}" for i in range(n)]
    pairs = ["QQQ", "SPY", "SOXL", "IWM", "TQQQ", "SOXX", "SMH", "RSP", "SOXS", "VOO"]
    stypes = ["trend_pullback", "ema_crossover", "macd_momentum", "rsi_mean_reversion",
              "bollinger_reversion", "breakout_retest", "donchian_breakout", "vwap_bounce",
              "atr_momentum_expansion", "supertrend_continuation"]

    bots_data = {names[i]: {"name": names[i], "status": "active", "strategy_id": i+1, "pair": pairs[i]} for i in range(n)}
    evaluations = {}
    for i in range(n):
        evaluations[names[i]] = {
            "verdict": "HOLD", "win_rate": random.uniform(40, 65),
            "profit_factor": random.uniform(0.8, 2.0), "total_trades": random.randint(5, 30),
            "pair": pairs[i], "strategy_type": stypes[i], "bot_status": "active",
            "adaptation_score": random.randint(40, 80), "reasons": ["Mock data"],
        }
    regime_info = {"regime": "trending_up", "confidence": 72}
    learning = {}
    summary = {"HOLD": 6, "PAUSE": 2, "REACTIVATE": 2}
    return bots_data, evaluations, regime_info, learning, summary, None
