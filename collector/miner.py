"""Minimal AxeOS (ESP-Miner) HTTP API client."""

import logging

import requests

logger = logging.getLogger(__name__)


class AxeOS:
    def __init__(self, ip: str, timeout: float = 10):
        self.ip = ip
        self.base = f"http://{ip}"
        self.timeout = timeout

    def info(self) -> dict:
        """GET /api/system/info — full telemetry snapshot."""
        resp = requests.get(f"{self.base}/api/system/info", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def patch_system(self, **fields) -> bool:
        """PATCH /api/system — update settings, e.g. frequency / coreVoltage."""
        resp = requests.patch(f"{self.base}/api/system", json=fields, timeout=self.timeout)
        resp.raise_for_status()
        logger.info("[%s] applied settings: %s", self.ip, fields)
        return True

    def restart(self) -> bool:
        """POST /api/system/restart — reboot the miner."""
        resp = requests.post(f"{self.base}/api/system/restart", timeout=self.timeout)
        resp.raise_for_status()
        logger.warning("[%s] restart requested", self.ip)
        return True
