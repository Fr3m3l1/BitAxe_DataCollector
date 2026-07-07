# BitAxe Data Collector

Local agent that runs next to your [Bitaxe](https://bitaxe.org) miner(s)
(e.g. on a Raspberry Pi). It polls each miner's AxeOS API, pushes the
telemetry to the [BitAxe_Dashboard](../BitAxe_Dashboard), and runs the
**auto-tuner** that optimizes frequency and core voltage.

The previous single-file forwarder is preserved under [`legacy/`](legacy/).

## Features

- Polls `GET /api/system/info` on every configured miner (default every 30 s)
  and pushes the samples to the dashboard.
- **Offline buffering** — when the dashboard or internet is unreachable,
  samples are buffered locally (up to ~7 days) and flushed on reconnect.
- **Auto-tuner** — enabled and configured from the dashboard UI:
  - hill-climbs frequency / core voltage toward the best **hashrate** or
    **efficiency (J/TH)**, measuring each operating point for a configurable
    window before deciding;
  - detects instability (hashrate below the firmware's expected value) and
    raises core voltage or backs off;
  - keeps the miner at or below the target temperature and **downclocks
    immediately** when the hard temperature/power limits are exceeded —
    this safety logic runs locally on every poll and never depends on the
    dashboard being reachable;
  - pauses and reports when AxeOS' own overheat mode trips;
  - remembers every tried operating point (with scores) across restarts and
    settles on the best known point when there is nothing better to try;
  - applies changes via `PATCH /api/system` and verifies them, optionally
    restarting the miner if a setting doesn't take effect live;
  - reports every action with its reason to the dashboard (Auto-Tuner tab).

## Running

```bash
cp .env.example .env   # set MINER_IPS, DASHBOARD_URL, API_KEY
pip install -r requirements.txt
python run.py
```

Or with Docker (see `docker.sh` for the ARM64 image build):

```bash
docker run -d --name bitaxe-collector --restart unless-stopped \
  --env-file .env -v bitaxe-collector-data:/data \
  fr3m3l/miner-data-collector:latest
```

## Configuration (environment)

| Variable | Default | Purpose |
|---|---|---|
| `MINER_IPS` | *(required)* | Comma-separated miner IPs |
| `DASHBOARD_URL` | *(required)* | Dashboard base URL |
| `API_KEY` | — | Must match the dashboard's `API_KEY` |
| `POLL_SECONDS` | `30` | Telemetry poll interval |
| `CONFIG_REFRESH_SECONDS` | `60` | Tuner-config refresh interval |
| `DATA_DIR` | `./data` (`/data` in Docker) | Buffer + tuner memory |
| `TUNER_FORCE_DISABLED` | `false` | Hard kill-switch: never touch miner settings |

All tuner parameters (mode, temperature targets, frequency/voltage ranges,
step sizes, measurement windows) are configured in the dashboard UI and
fetched from there.

## Safety model

1. AxeOS' own overheat protection stays untouched as the last line of defense.
2. The tuner's hard limits (`max temp`, `max VR temp`, `max power`) trigger an
   immediate local downclock — no measurement window, no dashboard round-trip.
3. The steady-state target (`target temp`) keeps normal operation several
   degrees below the hard limits.
4. `TUNER_FORCE_DISABLED=true` guarantees read-only behavior regardless of
   the dashboard configuration.
