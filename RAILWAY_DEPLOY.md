# Deploy Strategy Factory to Railway

Run the dashboard + auto-trader 24/7 on Railway. Open the dashboard each morning from your phone or laptop — everything trades on its own in the background.

## What gets deployed

- **Flask web server** on port `$PORT` (set by Railway)
- **Auto-trader background threads** for simulator and Alpaca, with Alpaca set to a 15-minute cycle by default
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
| `ALPACA_API_KEY` | your Alpaca paper API key | **YES for Alpaca trading** |
| `ALPACA_API_SECRET` | your Alpaca paper API secret | **YES for Alpaca trading** |
| `ALPACA_AUTO_TRADE_INTERVAL_MIN` | `15` | recommended |
| `AUTO_TRADE_INTERVAL_MIN` | `30` | optional simulator interval |
| `INTRADAY_GATE_ENABLED` | `true` | recommended |
| `RAILWAY_ENVIRONMENT` | leave as-is, Railway sets this automatically | auto |

If `DASHBOARD_PASSWORD` is missing, the service still starts, but auth is disabled. For Railway, set it so the dashboard is not exposed publicly.

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
[entrypoint] Bootstrap complete — starting gunicorn via exec...
[INFO] Starting gunicorn
[INFO] Listening at: http://0.0.0.0:$PORT
```

### Step 6 — Open your URL

Railway gives you a free subdomain like `strategy-factory-production.up.railway.app`.

1. Click the domain link in the service settings
2. Browser prompts for the username/password you set
3. Dashboard loads

### Step 7 — Enable Alpaca auto-trading

1. Open the **Alpaca** page
2. Click **Connect** and confirm the Alpaca paper account loads
3. Confirm the page shows a `15 min` auto-trading interval
4. Enable Alpaca auto-trading
5. Optionally click **⚡ Run Cycle Now** for the first pass

Within 15 minutes (or immediately if you clicked Run Now), the system will:
- Re-evaluate all 18 bots
- Regenerate the portfolio allocation
- Apply the intraday gate and rebalance your Alpaca paper account

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
| Dashboard opens without auth | Set `DASHBOARD_PASSWORD` in Railway Variables |
| Dashboard loads but Connect button fails | Check logs — Binance might be blocked in your Railway region. Move the service region in Settings. |
| Alpaca auto-trader never runs | Check logs for `AlpacaAutoTrader thread started`; ensure `ALPACA_AUTO_TRADE_INTERVAL_MIN=15` is set |
| State resets on every deploy | Verify the volume is mounted at `/data`, not a different path |
| Dashboard doesn't reflect recent runs | Force-refresh browser (`Cmd+Shift+R`) or wait for the page refresh; Alpaca live numbers update every second |
| Orders fail with "price fetch failed" | Binance API geo-blocks some regions — switch Railway region to US East |

## Updating the deployment

Push to GitHub → Railway auto-redeploys. Your volume (DB, state, positions) is preserved.

## Going back to local-only

Stop paying Railway and run `python3 run_paper_trading.py` on your laptop. Same system, just doesn't run when your laptop is off.
