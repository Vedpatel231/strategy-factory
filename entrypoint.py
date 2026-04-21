"""
Strategy Factory — Container entrypoint.

Idempotently bootstraps the service on first launch:
  1. Ensures data + reports directories exist (use STRATEGY_FACTORY_DATA_DIR)
  2. Seeds the SQLite database if it's empty
  3. Generates an initial dashboard so the URL responds right away

Safe to run every startup — all steps skip work that's already done.
"""

import os
import sys
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config


def seed_if_needed():
    if not os.path.exists(config.DB_PATH):
        print(f"[entrypoint] DB not found at {config.DB_PATH}, seeding...")
        r = subprocess.run([sys.executable, "seed_data.py"],
                           cwd=os.path.dirname(__file__),
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[entrypoint] Seeding FAILED:\n{r.stderr}")
            sys.exit(1)
        print("[entrypoint] DB seeded ✓")
    else:
        print(f"[entrypoint] DB already exists at {config.DB_PATH}")


def generate_dashboard_if_needed():
    dashboard_exists = os.path.exists(config.DASHBOARD_OUTPUT)
    source_files = ["generate_dashboard.py", "dashboard_server.py", "daily_runner.py"]
    repo_dir = os.path.dirname(__file__)
    source_mtime = max(
        os.path.getmtime(os.path.join(repo_dir, p))
        for p in source_files
        if os.path.exists(os.path.join(repo_dir, p))
    )
    dashboard_mtime = os.path.getmtime(config.DASHBOARD_OUTPUT) if dashboard_exists else 0

    if not dashboard_exists or source_mtime > dashboard_mtime:
        reason = "No dashboard yet" if not dashboard_exists else "Dashboard code changed"
        print(f"[entrypoint] {reason} — running daily_runner...")
        r = subprocess.run([sys.executable, "daily_runner.py"],
                           cwd=os.path.dirname(__file__),
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            print(f"[entrypoint] daily_runner FAILED:\n{r.stderr[-800:]}")
        else:
            print("[entrypoint] Dashboard generated ✓")
    else:
        print(f"[entrypoint] Dashboard exists at {config.DASHBOARD_OUTPUT}")


def main():
    print("=" * 60, flush=True)
    print("  🚀 Strategy Factory — Container bootstrap", flush=True)
    print("=" * 60, flush=True)
    print(f"  DATA_DIR: {config.DATA_DIR}", flush=True)
    print(f"  REPORT_DIR: {config.REPORT_DIR}", flush=True)
    print(f"  DB_PATH: {config.DB_PATH}", flush=True)
    print("=" * 60, flush=True)
    seed_if_needed()
    generate_dashboard_if_needed()
    print("[entrypoint] Bootstrap complete — starting gunicorn via exec...", flush=True)
    # Replace this Python process with gunicorn so stdio passes through cleanly.
    port = os.environ.get("PORT", "8765")
    os.execvp("gunicorn", [
        "gunicorn", "dashboard_server:app",
        "--bind", f"0.0.0.0:{port}",
        "--workers", "1",
        "--threads", "4",
        "--timeout", "240",
        "--access-logfile", "-",
        "--error-logfile", "-",
        "--log-level", "info",
    ])


if __name__ == "__main__":
    main()
