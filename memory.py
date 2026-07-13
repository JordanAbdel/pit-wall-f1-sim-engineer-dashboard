"""Engineer memory — remembers per-corner findings across laps.

Powers repeat-mistake detection ("Same again at Pouhon"), streak
coaching ("Third lap running"), fix acknowledgements ("Better there"),
the live advice line, driver-style chips, and the debrief's recurring
mistake. Zones are keyed by the reference lap's brake-onset distance,
rounded to 50 m so lap-to-lap jitter maps to the same corner.
"""
from typing import NamedTuple

STREAK_CALL = 3        # consecutive laps before it becomes a streak call
RECURRING_MIN = 3      # total flagged laps before something is "recurring"
CHIP_MIN = 4           # findings of one kind before it defines your style


class MemoryVerdict(NamedTuple):
    kind: str            # "first" | "repeat" | "streak"
    streak: int
    fixed_onset: float | None  # a previously-flagged zone that came good


class _Zone:
    def __init__(self):
        self.total = 0
        self.streak = 0
        self.last_lap = None
        self.last_cause = None
        self.magnitudes = []   # metres for braking causes, kph for apex speed
        self.nagged = False    # already flagged as recurring on the radio


class CornerMemory:
    def __init__(self):
        self._zones = {}
        self._gain_counts = {}   # cause -> laps it appeared
        self._loss_counts = {}

    @staticmethod
    def _key(onset_m: float) -> int:
        return int(round(onset_m / 50) * 50)

    def note_sector(self, lap_num: int, findings: list) -> MemoryVerdict | None:
        """Record this sector's findings; return the radio-relevant verdict
        for the worst loss (or a fix acknowledgement), if any."""
        fixed_onset = None
        losses = [f for f in findings if not f.gain]
        gains = [f for f in findings if f.gain]

        for f in gains:
            self._gain_counts[f.cause] = self._gain_counts.get(f.cause, 0) + 1
            zone = self._zones.get(self._key(f.onset_m))
            if zone and zone.last_lap == lap_num - 1 and zone.streak >= 2:
                fixed_onset = f.onset_m
                zone.streak = 0

        verdict = None
        for f in losses:
            self._loss_counts[f.cause] = self._loss_counts.get(f.cause, 0) + 1
            zone = self._zones.setdefault(self._key(f.onset_m), _Zone())
            if zone.last_lap == lap_num:
                continue  # sector-boundary zones can match twice per lap
            zone.streak = zone.streak + 1 if zone.last_lap == lap_num - 1 else 1
            zone.total += 1
            zone.last_lap = lap_num
            zone.last_cause = f.cause
            zone.magnitudes.append(abs(f.onset_diff_m)
                                   if f.cause == "braked_early"
                                   else abs(f.min_speed_diff))
        worst = max(losses, key=lambda f: f.score(), default=None)
        if worst is not None:
            zone = self._zones[self._key(worst.onset_m)]
            kind = ("streak" if zone.streak >= STREAK_CALL
                    else "repeat" if zone.streak == 2 else "first")
            verdict = MemoryVerdict(kind, zone.streak, fixed_onset)
        elif fixed_onset is not None:
            verdict = MemoryVerdict("fixed", 0, fixed_onset)
        return verdict

    def worst_zones(self, n: int = 5, min_laps: int = 2) -> list:
        """The zones costing the most time this session, worst first:
        [(onset_m, cause, avg_magnitude, laps), ...]. Ranked by how many
        laps a zone was flagged, then by average magnitude."""
        ranked = sorted(
            ((k, z) for k, z in self._zones.items() if z.total >= min_laps),
            key=lambda kz: (kz[1].total,
                            sum(kz[1].magnitudes) / len(kz[1].magnitudes)),
            reverse=True)
        return [(float(k), z.last_cause,
                 round(sum(z.magnitudes) / len(z.magnitudes)), z.total)
                for k, z in ranked[:n]]

    def top_issue(self):
        """(onset_m, cause, avg_magnitude, laps) for the most recurring
        loss zone, or None until something genuinely repeats."""
        worst = self.worst_zones(1, min_laps=RECURRING_MIN)
        return worst[0] if worst else None

    def profile_chips(self, consistency_ms=None, lap_count=0) -> list:
        """Driver-style labels, only once the evidence is real."""
        chips = []
        if self._loss_counts.get("braked_early", 0) >= CHIP_MIN:
            chips.append("EARLY ON BRAKES")
        if self._loss_counts.get("overslowed", 0) >= CHIP_MIN:
            chips.append("OVERSLOWING APEXES")
        if self._loss_counts.get("slow_exit", 0) >= CHIP_MIN:
            chips.append("WEAK TRACTION")
        if self._gain_counts.get("later_brakes", 0) >= CHIP_MIN:
            chips.append("LATE BRAKER")
        if self._gain_counts.get("carried_speed", 0) >= CHIP_MIN:
            chips.append("STRONG ENTRY")
        if consistency_ms is not None and lap_count >= 5:
            chips.append("CONSISTENT" if consistency_ms < 300 else "RAGGED")
        return chips[:3]
