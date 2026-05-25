"""
Strategy Factory — One-Command Launcher

Run each morning:
    python3 run_paper_trading.py

It will:
  1. Check that Flask is installed (offers to install if missing)
  2. Generate/refresh the dashboard if missing
  3. Start the local server at http://127.0.0.1:8765
  4. Open your browser

From the dashboard you can then click to initialize the local paper account,
preview orders, execute simulated trades, and track synthetic paper P&L,
and reset back to $1,000 anytime.
"""

import os
import sys
import subprocess
import webbrowser
import time


def banner(text, color="\033[96m"):
    print(f"\n{color}{'=' * 64}")
    print(f"  {text}")
    print(f"{'=' * 64}\033[0m")


def check_and_install(package_name, import_name=None):
    import_name = import_name or package_name
    try:
        __import__(import_name)
        print(f"✅ {package_name} is installed")
        return True
    except ImportError:
        print(f"❌ {package_name} not found.")
        resp = input(f"   Install it now? [Y/n]: ").strip().lower()
        if resp in ("", "y", "yes"):
            print(f"   Installing {package_name}...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package_name, "--quiet"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"✅ {package_name} installed")
                return True
            print(f"❌ Install failed:\n{result.stderr}")
            return False
        return False


def ensure_dashboard():
    path = os.path.join(os.path.dirname(__file__), "reports", "dashboard.html")
    if os.path.exists(path):
        print("✅ Dashboard already generated")
        return True
    print("   Dashboard not found — generating now...")
    result = subprocess.run(
        [sys.executable, "daily_runner.py"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print("✅ Dashboard generated")
        return True
    print(f"❌ daily_runner.py failed:\n{result.stderr[-800:]}")
    return False


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    banner("📊 STRATEGY FACTORY — DASHBOARD LAUNCHER")

    print("\n[1/3] Checking Python packages...")
    if not check_and_install("flask"):
        sys.exit(1)

    print("\n[2/3] Making sure dashboard is generated...")
    if not ensure_dashboard():
        sys.exit(1)

    print("\n[3/3] Starting local server on http://127.0.0.1:8765 ...")
    print("   (Press Ctrl+C to stop)")
    time.sleep(0.5)

    def delayed_open():
        time.sleep(1.2)
        try:
            webbrowser.open("http://127.0.0.1:8765/#alpaca")
        except Exception:
            pass

    import threading
    threading.Thread(target=delayed_open, daemon=True).start()

    banner("🚀 Server starting — browser will open automatically", "\033[92m")
    print("   Dashboard: http://127.0.0.1:8765/")
    print("   Simulator: local paper broker, $1,000 starting balance")
    print("   Price model: synthetic math-based simulation\n")

    from dashboard_server import app, HOST, PORT
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped. Re-run anytime with: python3 run_paper_trading.py")
        sys.exit(0)
