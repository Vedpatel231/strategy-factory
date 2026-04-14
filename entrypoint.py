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
    if not os.path.exists(config.DASHBOARD_OUTPUT):
        print("[entrypoint] No dashboard yet — running daily_runner...")
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
    print("=" * 60)
    print("  🚀 Strategy Factory — Container bootstrap")
    print("=" * 60)
    print(f"  DATA_DIR: {config.DATA_DIR}")
    print(f"  REPORT_DIR: {config.REPORT_DIR}")
    print(f"  DB_PATH: {config.DB_PATH}")
    print("=" * 60)
    seed_if_needed()
    generate_dashboard_if_needed()
    print("[entrypoint] Bootstrap complete — handing off to web server.")


if __name__ == "__main__":
    main()
