"""Lightweight .env loader — no external dependencies."""

import os


def load_env_file():
    """Auto-load .env file from project root if present."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                # Don't overwrite existing env vars (explicit exports take priority)
                if key and val and not os.getenv(key):
                    os.environ[key] = val
    except Exception:
        pass  # silently ignore; env loading is best-effort


# Auto-load on import
load_env_file()
