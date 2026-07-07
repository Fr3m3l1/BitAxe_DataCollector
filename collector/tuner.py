"""Auto-tuner for Bitaxe miners.

Hill-climbs frequency and core voltage toward the best hashrate or
efficiency (J/TH) while enforcing thermal guardrails. All safety logic
runs locally on every poll, so an unreachable dashboard can never leave
the miner in a hot state.

Behaviour per tick (one miner telemetry sample):

1. Overheat flag set by the firmware  -> hold everything, report once.
2. Hard limit breached (temp/VR/power) -> immediate downclock, no waiting.
3. Otherwise, after a settle period, collect samples for a measurement
   window and then decide the next operating point:
     - hashrate below what the firmware expects  -> more voltage (or less
       frequency at the voltage ceiling): the point is unstable.
     - hotter than target                        -> less voltage (efficiency
       mode) or less frequency.
     - cool and stable                           -> try the next step up
       (frequency first in hashrate mode, lower voltage first in
       efficiency mode), unless that point already failed before.
     - nothing left to try                       -> settle on the best
       point seen so far.

Every applied change is verified against the miner (with an optional
restart fallback) and reported to the dashboard as a tuner event.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Flags recorded per operating point. They expire so the tuner adapts when
# conditions change: a point that was too hot at summer noon deserves another
# chance on a cool night; real silicon instability is much less variable.
UNSTABLE = "unstable"   # hashrate did not follow frequency
THERMAL = "thermal"     # exceeded target temperature / power budget
FLAG_TTL = {THERMAL: 12 * 3600, UNSTABLE: 7 * 24 * 3600}

MIN_WINDOW_SAMPLES = 5
EMERGENCY_INTERVAL = 60      # s between emergency downclock steps
VERIFY_TIMEOUT = 45          # s to wait for a PATCH to take effect
RESTART_SETTLE_EXTRA = 90    # extra settle time after a restart
RETRY_AFTER = 12 * 3600      # s before a well-measured point may be re-explored


class Tuner:
    def __init__(self, mac: str, client, uplink, state_path: Path):
        self.mac = mac
        self.client = client          # AxeOS instance
        self.uplink = uplink
        self.state_path = state_path
        self.config: dict = {"enabled": False}

        self.history: dict[str, dict] = {}   # "freq:volt" -> {score, n, flags{name:ts}, last}
        self.settle_until = 0.0
        self.window: list[dict] = []
        self.pending = None                   # {"freq","volt","deadline","restarted"}
        self.last_emergency = 0.0
        self.overheat_reported = False
        self._load_state()

    # ------------------------------------------------------------- state

    def _load_state(self):
        try:
            data = json.loads(self.state_path.read_text())
            self.history = data.get("history", {})
        except (OSError, ValueError):
            return
        # Migrate pre-expiry state files (flags as a list, no last-visit time).
        now = time.time()
        for entry in self.history.values():
            if isinstance(entry.get("flags"), list):
                entry["flags"] = {f: now for f in entry["flags"]}
            entry.setdefault("last", now)

    def _save_state(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"history": self.history}))
        except OSError as e:
            logger.error("cannot save tuner state: %s", e)

    def set_config(self, config: dict):
        if config.get("enabled") and not self.config.get("enabled"):
            logger.info("[%s] tuner enabled: %s", self.mac, config)
            self.uplink.add_event(self.mac, "enabled", reason=f"mode={config.get('mode')}")
            self.settle_until = time.time() + 30  # let a fresh window build
            self.window = []
        elif self.config.get("enabled") and not config.get("enabled"):
            self.uplink.add_event(self.mac, "disabled", reason="disabled in dashboard settings")
        self.config = config

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _key(freq, volt) -> str:
        return f"{int(round(freq))}:{int(round(volt))}"

    def _entry(self, freq, volt) -> dict:
        return self.history.setdefault(
            self._key(freq, volt), {"score": None, "n": 0, "flags": {}, "last": 0.0})

    def _flags(self, freq, volt) -> list:
        """Flags that have not expired yet."""
        flags = self.history.get(self._key(freq, volt), {}).get("flags", {})
        now = time.time()
        return [f for f, ts in flags.items() if now - ts < FLAG_TTL.get(f, 0)]

    def _mark(self, freq, volt, flag):
        self._entry(freq, volt)["flags"][flag] = time.time()

    def _record_score(self, freq, volt, score):
        entry = self._entry(freq, volt)
        # Exponential moving average so re-visits refine the estimate.
        entry["score"] = score if entry["score"] is None else 0.6 * entry["score"] + 0.4 * score
        entry["n"] += 1
        entry["last"] = time.time()

    def _may_explore(self, freq, volt) -> bool:
        """A candidate point is worth (re-)trying if it has no active flags and
        is either barely measured or hasn't been visited for a while."""
        if self._flags(freq, volt):
            return False
        entry = self.history.get(self._key(freq, volt))
        if entry is None:
            return True
        return entry["n"] < 3 or time.time() - entry["last"] > RETRY_AFTER

    def _best_point(self):
        best = None
        for key, entry in self.history.items():
            if entry["score"] is None:
                continue
            freq, volt = (int(x) for x in key.split(":"))
            if self._flags(freq, volt):
                continue
            c = self.config
            if not (c["freq_min"] <= freq <= c["freq_max"] and c["volt_min"] <= volt <= c["volt_max"]):
                continue
            if best is None or entry["score"] > best[2]:
                best = (freq, volt, entry["score"])
        return best

    # ------------------------------------------------------------- actions

    def _apply(self, freq, volt, action, reason, details=""):
        c = self.config
        freq = max(c["freq_min"], min(c["freq_max"], int(round(freq))))
        volt = max(c["volt_min"], min(c["volt_max"], int(round(volt))))
        try:
            self.client.patch_system(frequency=freq, coreVoltage=volt)
        except Exception as e:
            logger.error("[%s] failed to apply %s/%s: %s", self.mac, freq, volt, e)
            self.uplink.add_event(self.mac, "apply_failed", freq, volt, reason=str(e))
            return
        self.pending = {"freq": freq, "volt": volt,
                        "deadline": time.time() + VERIFY_TIMEOUT, "restarted": False}
        self.window = []
        self.settle_until = time.time() + c["settle_seconds"]
        self.uplink.add_event(self.mac, action, freq, volt, reason=reason, details=details)
        self._save_state()

    def _verify_pending(self, info) -> bool:
        """Returns True while a pending change is still being confirmed."""
        if not self.pending:
            return False
        p = self.pending
        applied = (abs((info.get("frequency") or 0) - p["freq"]) < 1
                   and abs((info.get("coreVoltage") or 0) - p["volt"]) < 1)
        if applied:
            self.pending = None
            return False
        if time.time() < p["deadline"]:
            return True
        if self.config.get("allow_restart") and not p["restarted"]:
            try:
                self.client.restart()
                p["restarted"] = True
                p["deadline"] = time.time() + VERIFY_TIMEOUT + RESTART_SETTLE_EXTRA
                self.settle_until = time.time() + self.config["settle_seconds"] + RESTART_SETTLE_EXTRA
                self.uplink.add_event(self.mac, "restart_to_apply", p["freq"], p["volt"],
                                      reason="settings did not apply live, restarting miner")
            except Exception as e:
                logger.error("[%s] restart failed: %s", self.mac, e)
            return True
        self.uplink.add_event(self.mac, "apply_failed", p["freq"], p["volt"],
                              reason="setting never took effect")
        self.pending = None
        return False

    # ------------------------------------------------------------- main tick

    def tick(self, info: dict):
        c = self.config
        if not c.get("enabled"):
            return

        freq = info.get("frequency")
        volt = info.get("coreVoltage")
        temp = info.get("temp")
        vr_temp = info.get("vrTemp")
        power = info.get("power")
        if freq is None or volt is None or temp is None:
            return

        # 1. Firmware overheat protection tripped: hands off, report once.
        if info.get("overheat_mode"):
            if not self.overheat_reported:
                self.overheat_reported = True
                self.uplink.add_event(self.mac, "overheat_hold", freq, volt,
                                      reason="AxeOS overheat mode active — tuning paused "
                                             "until it is cleared in the miner UI")
            return
        self.overheat_reported = False

        # 2. Hard guardrails: act immediately, don't wait for a window.
        if (temp > c["max_temp"] or (vr_temp and vr_temp > c["max_vr_temp"])
                or (power and power > c["max_power"] * 1.10)):
            if time.time() - self.last_emergency > EMERGENCY_INTERVAL:
                self.last_emergency = time.time()
                self._mark(freq, volt, THERMAL)
                if freq - c["freq_step"] >= c["freq_min"]:
                    new_freq, new_volt = freq - 2 * c["freq_step"], volt
                else:
                    new_freq, new_volt = c["freq_min"], volt - c["volt_step"]
                self._apply(new_freq, new_volt, "emergency",
                            f"limits exceeded: temp {temp:.1f}°C"
                            + (f", VR {vr_temp:.1f}°C" if vr_temp else "")
                            + (f", {power:.1f}W" if power else ""))
            return

        if self._verify_pending(info):
            return
        if time.time() < self.settle_until:
            return

        # 3. Measurement window.
        self.window.append({
            "t": time.time(), "hash": info.get("hashRate") or 0,
            "expected": info.get("expectedHashrate"), "temp": temp,
            "vr": vr_temp or 0, "power": power or 0,
        })
        first = self.window[0]["t"]
        if time.time() - first < c["dwell_seconds"] or len(self.window) < MIN_WINDOW_SAMPLES:
            return

        self._decide(freq, volt)

    # ------------------------------------------------------------- decision

    def _decide(self, freq, volt):
        c = self.config
        w = self.window
        self.window = []
        n = len(w)
        avg_hash = sum(s["hash"] for s in w) / n
        avg_temp = sum(s["temp"] for s in w) / n
        avg_vr = sum(s["vr"] for s in w) / n
        avg_power = sum(s["power"] for s in w) / n
        expected_vals = [s["expected"] for s in w if s["expected"]]
        avg_expected = sum(expected_vals) / len(expected_vals) if expected_vals else None

        stable = avg_expected is None or avg_hash >= 0.90 * avg_expected
        score = (avg_hash / avg_power) if (c["mode"] == "efficiency" and avg_power) else avg_hash
        if stable:
            self._record_score(freq, volt, score)
        metrics = (f"avg {avg_hash:.0f} GH/s, {avg_temp:.1f}°C, {avg_power:.1f}W"
                   + (f", expected {avg_expected:.0f} GH/s" if avg_expected else ""))
        logger.info("[%s] window done @%d MHz/%d mV: %s", self.mac, freq, volt, metrics)

        step_f, step_v = c["freq_step"], c["volt_step"]

        def in_range(f, v):
            return c["freq_min"] <= f <= c["freq_max"] and c["volt_min"] <= v <= c["volt_max"]

        def clean(f, v):
            return in_range(f, v) and not self._flags(f, v)

        # Outside configured limits (e.g. config was tightened): clamp first.
        if not in_range(freq, volt):
            self._apply(freq, volt, "clamp", "operating point outside configured limits", metrics)
            return

        # Unstable: hashrate is not keeping up with the frequency.
        if not stable:
            self._mark(freq, volt, UNSTABLE)
            if in_range(freq, volt + step_v):
                self._apply(freq, volt + step_v, "step",
                            f"hashrate {100 * avg_hash / avg_expected:.0f}% of expected — raising core voltage",
                            metrics)
            else:
                self._apply(freq - step_f, volt, "step",
                            "unstable at maximum voltage — lowering frequency", metrics)
            return

        # Too hot / over power budget for steady state.
        if avg_temp > c["target_temp"] or avg_vr > c["max_vr_temp"] - 2 or avg_power > c["max_power"]:
            self._mark(freq, volt, THERMAL)
            if c["mode"] == "efficiency" and clean(freq, volt - step_v):
                self._apply(freq, volt - step_v, "step",
                            f"avg temp {avg_temp:.1f}°C above target — reducing core voltage", metrics)
            else:
                self._apply(freq - step_f, volt, "step",
                            f"avg temp {avg_temp:.1f}°C above target — reducing frequency", metrics)
            return

        # Cool and stable: explore, mode decides the direction preference.
        headroom = avg_temp <= c["target_temp"] - 2
        candidates = []
        if c["mode"] == "efficiency":
            candidates.append((freq, volt - step_v, "trying lower core voltage for better efficiency"))
            if headroom:
                candidates.append((freq + step_f, volt, "thermal headroom — trying higher frequency"))
                candidates.append((freq + step_f, volt + step_v, "trying higher frequency with more voltage"))
        else:
            if headroom:
                candidates.append((freq + step_f, volt, "thermal headroom — trying higher frequency"))
                candidates.append((freq + step_f, volt + step_v, "trying higher frequency with more voltage"))
        for f, v, reason in candidates:
            if in_range(f, v) and self._may_explore(f, v):
                self._apply(f, v, "step", reason, metrics)
                return

        # Nothing new to try: sit on the best point we know.
        best = self._best_point()
        if best and (best[0] != freq or best[1] != volt) and best[2] > score * 1.02:
            self._apply(best[0], best[1], "settle",
                        f"returning to best known point (score {best[2]:.2f})", metrics)
        else:
            self._save_state()
            logger.info("[%s] holding optimum %d MHz / %d mV", self.mac, freq, volt)
