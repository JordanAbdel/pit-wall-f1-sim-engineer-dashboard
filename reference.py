"""Reference times — what the coaching compares against.

Priority order:
  rival   — the Time Trial rival ghost (the only online times the game
            broadcasts): real lap + sector splits, live.
  target  — per-track target lap from benchmarks.json; sector targets are
            derived by splitting the target lap in the driver's own
            session-best sector proportions (needs one full lap first).
  self    — the driver's own session bests (the original behaviour).

Corner-cause analysis (trace.py) always compares against the driver's own
laps — no source exposes other drivers' distance-indexed telemetry — so
this module only owns the TIMES the deltas are measured against.
"""
import json
import logging

import config

log = logging.getLogger("reference")


def _load_benchmarks() -> dict:
    for path in (config.APP_DIR / "benchmarks.json",
                 config.BUNDLE_DIR / "benchmarks.json"):
        try:
            raw = json.loads(path.read_text())
            return {int(k): v for k, v in raw.items() if not k.startswith("_")}
        except FileNotFoundError:
            continue
        except Exception:
            log.exception("benchmarks.json unreadable at %s", path)
    return {}


BENCHMARKS = _load_benchmarks()


class ReferenceTimes:
    def __init__(self):
        self._rival_lap = None
        self._rival_sectors = None    # (s1, s2, s3) ms, or None
        self._target_lap = None
        self._own_best_sectors = None  # (s1, s2, s3) ms — for target splits

    def set_track(self, track_id) -> None:
        bench = BENCHMARKS.get(track_id)
        self._target_lap = bench["target_lap_ms"] if bench else None

    def update_rival(self, rival) -> None:
        """From the Time Trial packet's rival data set."""
        if rival.lap_time_ms > 0:
            self._rival_lap = rival.lap_time_ms
            if (rival.sector1_ms > 0 and rival.sector2_ms > 0
                    and rival.sector3_ms > 0):
                self._rival_sectors = (rival.sector1_ms, rival.sector2_ms,
                                       rival.sector3_ms)

    def update_own(self, best_sectors: dict) -> None:
        """Track the driver's session-best sectors (analyzer format
        {1: ms|None, ...}) so a lap-only target can be split per sector."""
        if all(best_sectors.values()):
            self._own_best_sectors = (best_sectors[1], best_sectors[2],
                                      best_sectors[3])

    @property
    def source(self) -> str:
        if self._rival_lap:
            return "rival"
        if self._target_lap:
            return "target"
        return "self"

    def label(self) -> str:
        return {"rival": "the rival", "target": "the target"}.get(
            self.source, "your best")

    def lap_target(self):
        return self._rival_lap or self._target_lap

    def sector_target(self, sector: int):
        """1-based sector target in ms, or None when the current source
        can't provide one."""
        if self._rival_sectors:
            return self._rival_sectors[sector - 1]
        if self._target_lap and self._own_best_sectors:
            total = sum(self._own_best_sectors)
            if total > 0:
                return round(self._target_lap
                             * self._own_best_sectors[sector - 1] / total)
        return None

    def state(self) -> dict:
        """Snapshot for the dashboard."""
        sectors = None
        if self.source != "self":
            s = [self.sector_target(i) for i in (1, 2, 3)]
            if all(s):
                sectors = s
        return {"source": self.source, "lapMs": self.lap_target(),
                "sectors": sectors}
