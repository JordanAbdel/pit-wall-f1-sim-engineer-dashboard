"""Reference times — what the coaching compares against.

Priority order:
  rival   — the Time Trial rival ghost (the only online times the game
            broadcasts): real lap + sector splits, live.
  target  — per-track target lap from benchmarks.json; sector targets are
            derived by splitting the target lap in the driver's own
            session-best sector proportions (needs one full lap first).
            These are alien/leaderboard times, so they are only used once
            the driver is within MAX_STRETCH of them — see _credible().
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

# The benchmark table is top-of-the-leaderboard pace. A reference is only
# coaching if it is reachable: told "you're 2.4 seconds down" in every sector
# of every lap, a driver learns nothing, because the number never moves and
# no corner explains it. Beyond this much, drop back to the driver's own
# bests, where a delta means something they can act on this lap. It also
# covers the layouts that changed between game years (Albert Park was
# revised for 2022) — one number per track cannot be right for both, and
# the wrong one fails this test instead of being recited.
MAX_STRETCH = 0.04          # target may be at most 4% under demonstrated pace


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

    def _credible(self) -> bool:
        """Whether the static target is close enough to the driver's own
        demonstrated pace to be worth chasing."""
        if not self._target_lap:
            return False
        if not self._own_best_sectors:
            return False        # nothing to judge it against yet
        best = sum(self._own_best_sectors)
        return self._target_lap >= best * (1.0 - MAX_STRETCH)

    @property
    def source(self) -> str:
        if self._rival_lap:
            return "rival"
        if self._credible():
            return "target"
        return "self"

    def label(self) -> str:
        return {"rival": "the rival", "target": "the target"}.get(
            self.source, "your best")

    def lap_target(self):
        if self._rival_lap:
            return self._rival_lap
        return self._target_lap if self._credible() else None

    def sector_target(self, sector: int):
        """1-based sector target in ms, or None when the current source
        can't provide one."""
        if self._rival_sectors:
            return self._rival_sectors[sector - 1]
        if self._credible():
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
                "sectors": sectors, "goalMs": self._target_lap}
