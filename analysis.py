"""Analysis engine — sector/lap deltas vs. session personal bests, plus
stint-pace deltas vs. the median of recent laps (the race reference).

Pure computation: events in, structured results out. No text, no voice.
"""
import statistics
from collections import deque
from typing import NamedTuple

from state import LapComplete, SectorComplete

STINT_WINDOW = 5           # laps in the rolling stint reference
STINT_OUTLIER_MS = 5000    # a sector this far off the median is an off, not pace


class SectorResult(NamedTuple):
    lap_num: int
    sector: int
    time_ms: int
    delta_ms: int | None     # vs. best before this sector; None = no baseline yet
    is_best: bool
    lap_invalid: bool
    stats: object = None       # TelemetryStats for this sector, if collected
    best_stats: object = None  # TelemetryStats of the best sector it was compared to
    stint_delta_ms: int | None = None  # vs. median of recent laps (race pace)


class LapResult(NamedTuple):
    lap_num: int
    lap_time_ms: int
    delta_ms: int | None     # vs. best lap before this one; None = no baseline yet
    is_best: bool
    invalid: bool
    worst_sector: int | None  # sector where most time was lost this lap, if any
    stint_delta_ms: int | None = None  # vs. median of recent laps (race pace)


def valid_laps(laps: list) -> list:
    """The laps a best may come from: deleted laps never count while any
    valid lap exists. With nothing valid yet (Time Trial deletes most
    attempts) the invalid ones stand in, mirroring the Analyzer's
    provisional-baseline rule."""
    valid = [l for l in laps if not l.get("invalid")]
    return valid or laps


def session_recap(laps: list) -> dict | None:
    """Computed view over the session's lap list (dashboard format:
    [{"lap": n, "sectors": [ms, ms, ms], "invalid": bool}, ...]).
    No new data pipeline."""
    if not laps:
        return None
    times = [sum(l["sectors"]) for l in laps]
    pool = valid_laps(laps)
    best_lap_ms = min(sum(l["sectors"]) for l in pool)
    best_sectors = [min(l["sectors"][i] for l in pool) for i in range(3)]
    # Weakest sector = biggest typical gap to its session best (median
    # across laps, so one off doesn't decide it).
    median_gaps = [round(statistics.median(l["sectors"][i] - best_sectors[i]
                                           for l in laps)) for i in range(3)]
    weakest = max(range(3), key=lambda i: median_gaps[i])
    strongest = min(range(3), key=lambda i: median_gaps[i])
    clean = [t for t in times if t - best_lap_ms < STINT_OUTLIER_MS]
    consistency_ms = round(statistics.pstdev(clean)) if len(clean) >= 2 else None
    improvement_ms = None
    if len(clean) >= 4:
        head = clean[:3]
        tail = clean[-3:]
        improvement_ms = round(sum(head) / len(head) - sum(tail) / len(tail))
    return {
        "laps": len(laps),
        "best_lap_ms": best_lap_ms,
        # False when every lap was deleted and the best is a stand-in —
        # never quote it later as "your best here".
        "best_lap_valid": not pool[0].get("invalid"),
        "theoretical_ms": sum(best_sectors),
        "weakest_sector": weakest + 1,
        "weakest_gap_ms": median_gaps[weakest],
        "strongest_sector": strongest + 1,
        "consistency_ms": consistency_ms,
        "improvement_ms": improvement_ms,
    }


class Analyzer:
    def __init__(self):
        self.best_sectors = {1: None, 2: None, 3: None}
        self.best_sector_stats = {1: None, 2: None, 3: None}
        # Whether each sector baseline came from a valid lap. A baseline set
        # from an invalid lap is provisional: any valid time replaces it.
        self.best_sector_valid = {1: False, 2: False, 3: False}
        self.best_lap_ms = None
        self._current_lap_deltas = {}
        # Sector bests this lap took as valid, with what they replaced. The
        # game can delete a lap after S1/S2 have closed (a cut in S3), and
        # then those sectors must not stand as valid bests.
        self._taken_this_lap = {}
        self._taken_lap_num = None
        self._recent_sectors = {1: deque(maxlen=STINT_WINDOW),
                                2: deque(maxlen=STINT_WINDOW),
                                3: deque(maxlen=STINT_WINDOW)}
        self._recent_laps = deque(maxlen=STINT_WINDOW)

    @staticmethod
    def _stint_delta(recent, time_ms):
        """Delta vs. the rolling median; appends unless it's an outlier
        (an off shouldn't drag the stint reference)."""
        delta = (time_ms - round(statistics.median(recent))
                 if len(recent) >= 2 else None)
        if delta is None or delta < STINT_OUTLIER_MS:
            recent.append(time_ms)
        return delta

    def seed_bests(self, s1_ms: int, s2_ms: int, s3_ms: int, lap_ms: int) -> None:
        """Adopt bests from the game's Session History (e.g. when the listener
        joins mid-session). Callers pass 0 for any time the game flagged
        invalid, so every seeded time is a valid baseline: it replaces a
        provisional one outright, and otherwise only ever lowers a best."""
        for sector, t in ((1, s1_ms), (2, s2_ms), (3, s3_ms)):
            if t <= 0:
                continue
            best = self.best_sectors[sector]
            if best is None or not self.best_sector_valid[sector] or t < best:
                self.best_sectors[sector] = t
                self.best_sector_valid[sector] = True
        if lap_ms > 0 and (self.best_lap_ms is None or lap_ms < self.best_lap_ms):
            self.best_lap_ms = lap_ms

    def on_sector(self, ev: SectorComplete) -> SectorResult:
        if ev.lap_num != self._taken_lap_num:
            # A lap abandoned without completing (flashback, restart) must
            # not have its takes revoked by some later deleted lap.
            self._taken_this_lap = {}
            self._taken_lap_num = ev.lap_num
        best = self.best_sectors[ev.sector]
        prev_best_stats = self.best_sector_stats[ev.sector]
        delta = None if best is None else ev.time_ms - best
        is_best = not ev.lap_invalid and (best is None or ev.time_ms < best)
        # An invalid lap may still seed the baseline when nothing valid
        # exists yet. Time Trial deletes most attempts for track limits, and
        # with no baseline at all there is no delta and so no coaching for
        # that sector — which is how a sector you always run wide in ends up
        # never being coached. Mirrors the trace reference rule: a valid time
        # always outranks a provisional one, regardless of which is faster.
        have_valid = self.best_sector_valid[ev.sector]
        if ev.lap_invalid:
            take = not have_valid and (best is None or ev.time_ms < best)
        else:
            take = best is None or not have_valid or ev.time_ms < best
        if take:
            if not ev.lap_invalid:
                self._taken_this_lap[ev.sector] = (
                    best, have_valid, self.best_sector_stats[ev.sector])
            self.best_sectors[ev.sector] = ev.time_ms
            self.best_sector_valid[ev.sector] = not ev.lap_invalid
            if ev.stats is not None:
                self.best_sector_stats[ev.sector] = ev.stats
        self._current_lap_deltas[ev.sector] = delta
        stint_delta = self._stint_delta(self._recent_sectors[ev.sector], ev.time_ms)
        return SectorResult(ev.lap_num, ev.sector, ev.time_ms, delta, is_best,
                            ev.lap_invalid, ev.stats, prev_best_stats, stint_delta)

    def on_lap(self, ev: LapComplete) -> LapResult:
        best = self.best_lap_ms
        delta = None if best is None else ev.lap_time_ms - best
        is_best = not ev.invalid and (best is None or ev.lap_time_ms < best)
        if is_best:
            self.best_lap_ms = ev.lap_time_ms
        if ev.invalid and ev.lap_num == self._taken_lap_num:
            self._revoke_sector_bests()
        self._taken_this_lap = {}

        losses = {s: d for s, d in self._current_lap_deltas.items() if d is not None and d > 0}
        worst_sector = max(losses, key=losses.get) if losses else None
        self._current_lap_deltas = {}
        stint_delta = self._stint_delta(self._recent_laps, ev.lap_time_ms)
        return LapResult(ev.lap_num, ev.lap_time_ms, delta, is_best, ev.invalid,
                         worst_sector, stint_delta)

    def _revoke_sector_bests(self) -> None:
        """The lap was deleted after some of its sectors had already been
        taken as valid bests. Put back the valid best each one replaced;
        where there wasn't one, keep the faster time as a provisional
        baseline so the sector still gets coached."""
        for sector, (prev, prev_valid, prev_stats) in self._taken_this_lap.items():
            if prev_valid:
                self.best_sectors[sector] = prev
                self.best_sector_stats[sector] = prev_stats
            else:
                if prev is not None and prev < self.best_sectors[sector]:
                    self.best_sectors[sector] = prev
                    self.best_sector_stats[sector] = prev_stats
                self.best_sector_valid[sector] = False
