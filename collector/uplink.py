"""Buffered uplink to the dashboard: samples, tuner events, config fetch.

Samples are buffered in memory and persisted to disk, so nothing is lost
when the dashboard or the internet link is down — the backlog is flushed
once connectivity returns.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

MAX_BUFFERED_SAMPLES = 20000  # ~7 days at 30s polling
FLUSH_BATCH = 500


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


class Uplink:
    def __init__(self, dashboard_url: str, api_key: str, data_dir: Path):
        self.url = dashboard_url
        self.headers = {"X-API-Key": api_key} if api_key else {}
        data_dir.mkdir(parents=True, exist_ok=True)
        self.samples_path = data_dir / "sample_buffer.json"
        self.events_path = data_dir / "event_buffer.json"
        self.samples = self._load(self.samples_path)
        self.events = self._load(self.events_path)

    @staticmethod
    def _load(path: Path) -> list:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return []

    def _persist(self):
        try:
            self.samples_path.write_text(json.dumps(self.samples[-MAX_BUFFERED_SAMPLES:]))
            self.events_path.write_text(json.dumps(self.events[-1000:]))
        except OSError as e:
            logger.error("failed to persist buffers: %s", e)

    def add_sample(self, info: dict, ts: str | None = None):
        self.samples.append({"ts": ts or utcnow(), "info": info})
        if len(self.samples) > MAX_BUFFERED_SAMPLES:
            del self.samples[: len(self.samples) - MAX_BUFFERED_SAMPLES]

    def add_event(self, mac: str, action: str, frequency=None, core_voltage=None,
                  reason: str = "", details: str = ""):
        self.events.append({
            "ts": utcnow(), "mac": mac, "action": action,
            "frequency": frequency, "core_voltage": core_voltage,
            "reason": reason, "details": details,
        })
        logger.info("tuner event [%s] %s: %s", mac, action, reason)

    def flush(self):
        """Push all buffered samples and events; keep what fails."""
        try:
            while self.samples:
                batch = self.samples[:FLUSH_BATCH]
                resp = requests.post(f"{self.url}/api/ingest", json={"samples": batch},
                                     headers=self.headers, timeout=15)
                resp.raise_for_status()
                del self.samples[:len(batch)]
            while self.events:
                batch = self.events[:100]
                resp = requests.post(f"{self.url}/api/collector/events", json={"events": batch},
                                     headers=self.headers, timeout=15)
                resp.raise_for_status()
                del self.events[:len(batch)]
        except requests.RequestException as e:
            logger.warning("dashboard unreachable, %d samples / %d events buffered (%s)",
                           len(self.samples), len(self.events), e)
        finally:
            self._persist()

    def fetch_config(self) -> dict | None:
        """Fetch tuner configuration from the dashboard. None when unreachable."""
        try:
            resp = requests.get(f"{self.url}/api/collector/config",
                                headers=self.headers, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning("config fetch failed: %s", e)
            return None
