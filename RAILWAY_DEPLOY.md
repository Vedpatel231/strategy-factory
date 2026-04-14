# Deploy Strategy Factory to Railway

Run the dashboard + auto-trader 24/7 on Railway. Open the dashboard each morning from your phone or laptop — everything trades on its own in the background.

## What gets deployed

- **Flask web server** on port `$PORT` (set by Railway)
- **Auto-trader background thread** that re-analyzes bots and rebalances every 30 minutes
- **Persistent volume** at `/data` holding SQLite DB, paper account state, learning state, logs
- **HTTP Basic Auth** protecting the public URL (required on Railway)

## One-time setup

### Step 1 — Push this folder to GitHub

Commit everything in `strategy_factory/` to a GitHub repo. **Do not commit `.env`** (it's in `.dockerignore` but double-check).

```bash
cd strategy_factory
git init
git add .
git commit -m "Strategy Factory v3.0"
git remote add origin git@github.com:YOUR_USERNAME/strategy-factory.git
git push -u origin main
```

### Step 2 — Create a new Railway project

1. Go to [railway.app/new](https://railway.app/new)
2. Choose **Deploy from GitHub repo**
3. Select your `strategy-factory` repo
4. Railway auto-detects the Dockerfile and starts building

### Step 3 — Add a persistent volume

Your SQLite DB and paper account state need to survive redeploys.

1. In your Railway project, click **+ New** → **Volume**
2. **Mount path**: `/data`
3. **Size**: `1 GB` is plenty
4. Attach it to your service

### Step 4 — Set environment variables

In the service **Variables** tab, add:

| Variable | Value | Required? |
|---|---|---|
| `DASHBOARD_PASSWORD` | pick a strong password (12+ chars) | **YES** |
| `DASHBOARD_USERNAME` | `admin` or whatever you like | optional |
| `STRATEGY_FACTORY_DATA_DIR` | `/data` | **YES** |
| `STRATEGY_FACTORY_REPORT_DIR` | `/data/reports` | **YES** |
| `AUTO_TRADE_INTERVAL_MIN` | `30` | optional |
| `RAILWAY_ENVIRONMENT` | leave as-is, Railway sets this automatically | auto |

**If `DASHBOARD_PASSWORD` is missing, the service will refuse to start** — this is a safety check so your trading panel can never be exposed to strangers.

### Step 5 — Wait for the build to finish

Railway builds the Dockerfile, which:
1. Installs Flask + gunicorn + numpy
2. Runs `entrypoint.py` to seed the DB and generate the first dashboard
3. Launches gunicorn serving the dashboard on `$PORT`

Check the **Deploy Logs** tab. You should see:

```
🚀 Strategy Factory — Container bootstrap
[entrypoint] DB not found at /data/strategy_factory.db, seeding...
[entrypoint] DB seeded ✓
[entrypoint] Dashboard generated ✓
[entrypoint] Bootstrap complete — handing off to web server.
[INFO] Starting gunicorn
[INFO] Listening at: http://0.0.0.0:8765
```

### Step 6 — Open your URL

Railway gives you a free subdomain like `strategy-factory-production.up.railway.app`.

1. Click the domain link in the service settings
2. Browser prompts for the username/password you set
3. Dashboard loads

### Step 7 — Enable auto-trading

1. Click the **💵 Paper Trading** tab
2. Click **🔌 Connect** (initializes the $1,000 paper account)
3. Scroll to **🤖 Auto-Trading** section
4. Click **▶️ Enable Auto-Trading**
5. Optionally click **⚡ Run Cycle Now** for the first pass

Within 30 minutes (or immediately if you clicked Run Now), the system will:
- Re-evaluate all 18 bots
- Regenerate the portfolio allocation
- Rebalance your paper account to match

From now on, check the dashboard each morning to see overnight P&L.

## Daily usage after deployment

- **Morning routine**: open the URL on your phone → Paper Trading tab → review P&L
- **Intervention**: click **⏸️ Disable Auto-Trading** if markets go wild and you want it to stop
- **Reset**: click **🔄 Reset to $1000** to wipe positions and start fresh
- **Force refresh**: click **⚡ Run Cycle Now** to trigger an out-of-band analysis

## Cost estimate

- **Free $5 trial**: deployments use ~$3-5/mo for this service (1GB volume + small container)
- **Hobby plan ($5/mo)**: plenty for this service plus your other 2 bots
- **Volume storage**: 1 GB costs ~$0.25/mo

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DASHBOARD_PASSWORD is required when deployed` | Add it as an env var |
| Dashboard loads but Connect button fails | Check logs — Binance might be blocked in your Railway region. Move the service region in Settings. |
| Auto-trader never runs | Check logs for `AutoTrader thread started`; ensure `AUTO_TRADE_INTERVAL_MIN` is set |
| State resets on every deploy | Verify the volume is mounted at `/data`, not a different path |
| Dashboard doesn't reflect recent runs | Force-refresh browser (`Cmd+Shift+R`) or wait 30s for the auto-refresh |
| Orders fail with "price fetch failed" | Binance API geo-blocks some regions — switch Railway region to US East |

## Updating the deployment

Push to GitHub → Railway auto-redeploys. Your volume (DB, state, positions) is preserved.

## Going back to local-only

Stop paying Railway and run `python3 run_paper_trading.py` on your laptop. Same system, just doesn't run when your laptop is off.
