"""Collector main loop: poll miners, push to the dashboard, run the tuner."""

import logging
import signal
import sys
import time

from . import config
from .miner import AxeOS
from .tuner import Tuner
from .uplink import Uplink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main():
    uplink = Uplink(config.DASHBOARD_URL, config.API_KEY, config.DATA_DIR)
    miners = {ip: {"client": AxeOS(ip), "tuner": None, "mac": None} for ip in config.MINER_IPS}
    tuner_configs = {}
    last_config_fetch = 0.0

    def shutdown(sig, frame):
        logger.info("shutting down, persisting buffers…")
        uplink.flush()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("collector started: miners=%s dashboard=%s poll=%ss tuner_kill_switch=%s",
                config.MINER_IPS, config.DASHBOARD_URL, config.POLL_SECONDS,
                config.TUNER_FORCE_DISABLED)

    while True:
        cycle_start = time.time()

        # Refresh tuner config from the dashboard.
        if time.time() - last_config_fetch > config.CONFIG_REFRESH_SECONDS:
            fetched = uplink.fetch_config()
            if fetched is not None:
                tuner_configs = fetched.get("tuners", {})
                last_config_fetch = time.time()

        for ip, m in miners.items():
            try:
                info = m["client"].info()
            except Exception as e:
                logger.warning("[%s] unreachable: %s", ip, e)
                continue

            uplink.add_sample(info)

            mac = info.get("macAddr") or info.get("hostname") or ip
            if m["tuner"] is None or m["mac"] != mac:
                m["mac"] = mac
                safe_mac = mac.replace(":", "-")
                m["tuner"] = Tuner(mac, m["client"], uplink,
                                   config.DATA_DIR / f"tuner_{safe_mac}.json")

            cfg = dict(tuner_configs.get(mac, tuner_configs.get("default", {"enabled": False})))
            if config.TUNER_FORCE_DISABLED:
                cfg["enabled"] = False
            m["tuner"].set_config(cfg)

            try:
                m["tuner"].tick(info)
            except Exception:
                logger.exception("[%s] tuner tick failed", ip)

        uplink.flush()

        elapsed = time.time() - cycle_start
        time.sleep(max(1.0, config.POLL_SECONDS - elapsed))


if __name__ == "__main__":
    main()
