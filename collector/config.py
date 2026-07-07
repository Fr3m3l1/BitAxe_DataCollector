"""Environment-based configuration for the collector."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Error: {name} environment variable is not set")
    return value


# Comma-separated list of miner IPs/hostnames, e.g. "192.168.1.50,192.168.1.51"
MINER_IPS = [ip.strip() for ip in _require("MINER_IPS").split(",") if ip.strip()]

# Base URL of the dashboard, e.g. "https://mining.example.com"
DASHBOARD_URL = _require("DASHBOARD_URL").rstrip("/")

# Must match the dashboard's API_KEY
API_KEY = os.getenv("API_KEY", "")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
CONFIG_REFRESH_SECONDS = int(os.getenv("CONFIG_REFRESH_SECONDS", "60"))

# Local state (sample buffer, tuner memory) — mount as a volume in Docker.
DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))

# Hard kill-switch: when true, the tuner never touches the miner regardless
# of what the dashboard config says.
TUNER_FORCE_DISABLED = os.getenv("TUNER_FORCE_DISABLED", "false").lower() in ("1", "true", "yes")
