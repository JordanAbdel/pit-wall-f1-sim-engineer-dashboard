"""Lap traces — distance-indexed speed/brake/throttle, corner-level comparison.

Every telemetry sample is recorded against lap_distance. When a sector
comes in slower than the session best, the current lap's braking zones
in that sector are matched against the best observed lap's, and the
worst mismatch is classified:

  braked_early — brake onset meaningfully earlier than the best lap
  overslowed   — minimum corner speed well below the best lap's
  slow_exit    — back on full throttle meaningfully later

The game never broadcasts corner names, so zones are named from
TRACK_CORNERS (per-track, distance -> turn name, approximate) with a
"the braking zone X k in" fallback.
"""
from typing import NamedTuple

BRAKE_ON = 0.35        # brake fraction that opens a braking zone
BRAKE_OFF = 0.15       # hysteresis to close it
THROTTLE_RESUME = 0.9
MIN_ZONE_GAP_M = 120.0
ZONE_WINDOW_M = 400.0  # how far past onset to look for apex/exit
MATCH_TOLERANCE_M = 150.0

# Thresholds for calling out a difference vs. the best lap
EARLY_BRAKE_M = 15.0
OVERSLOW_KPH = 6
LATE_THROTTLE_M = 25.0

# Approximate braking-turn positions by lap distance (metres from the
# line). Only tracks we're confident about; everything else gets the
# generic zone name. Track ids match packets.TRACK_NAMES.
TRACK_CORNERS = {
    10: [  # Spa-Francorchamps (~7004 m)
        (340, "turn one, La Source"),
        (2050, "turn five, Les Combes"),
        (2750, "turn eight, Rivage"),
        (3390, "turn ten, Pouhon"),
        (4150, "turn twelve, Fagnes"),
        (4900, "turn fifteen, Stavelot"),
        (6580, "the final chicane"),
    ],
}


def turn_name(track_id, onset_m: float) -> str:
    for dist, name in TRACK_CORNERS.get(track_id, ()):
        if abs(onset_m - dist) <= 300:
            return name
    return f"the braking zone {onset_m / 1000:.1f} k in"


def turn_label(track_id, onset_m: float) -> str:
    """Short UI label: 'T10' or '2.1k'."""
    for dist, name in TRACK_CORNERS.get(track_id, ()):
        if abs(onset_m - dist) <= 300:
            if name.startswith("turn "):
                word = name.split(",")[0].removeprefix("turn ").strip()
                numbers = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                           "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                           "eleven": 11, "twelve": 12, "thirteen": 13,
                           "fourteen": 14, "fifteen": 15, "sixteen": 16,
                           "seventeen": 17, "eighteen": 18, "nineteen": 19}
                if word in numbers:
                    return f"T{numbers[word]}"
            return name.split(",")[-1].strip().title()
    return f"{onset_m / 1000:.1f}k"


class BrakingZone(NamedTuple):
    onset_m: float
    min_speed: int
    resume_m: float | None   # where full throttle resumed, if seen


class CornerFinding(NamedTuple):
    onset_m: float           # best lap's onset — the reference point
    cause: str               # braked_early | overslowed | slow_exit
                             # | carried_speed | later_brakes | earlier_throttle
    onset_diff_m: float      # current - best (negative = earlier)
    min_speed_diff: int      # current - best (negative = slower apex)
    gain: bool = False       # True = the current lap was BETTER here
    confidence: str = "high"  # "high" | "low" — soften phrasing when low

    def score(self) -> float:
        """Rough magnitude for ranking findings — not a time estimate."""
        return abs(self.min_speed_diff) * 2 + abs(self.onset_diff_m) / 5


class LapTrace:
    def __init__(self):
        self.dist = []
        self.speed = []
        self.throttle = []
        self.brake = []
        self.lap_time_ms = None

    def add(self, dist_m: float, speed: int, throttle: float, brake: float) -> None:
        if dist_m < 0:
            return
        if self.dist and dist_m < self.dist[-1]:
            return  # flashback rewind — skip until distance recovers
        self.dist.append(dist_m)
        self.speed.append(speed)
        self.throttle.append(throttle)
        self.brake.append(brake)

    @property
    def max_dist(self) -> float:
        return self.dist[-1] if self.dist else 0.0

    def braking_zones(self, d0: float, d1: float) -> list:
        zones = []
        braking = False
        onset = min_speed = None
        resume = None
        for i in range(len(self.dist)):
            d = self.dist[i]
            if d < d0:
                continue
            if d > d1 + ZONE_WINDOW_M:
                break
            if not braking:
                if (self.brake[i] >= BRAKE_ON and d <= d1
                        and (not zones or d - zones[-1].onset_m >= MIN_ZONE_GAP_M)):
                    braking = True
                    onset, min_speed, resume = d, self.speed[i], None
            else:
                min_speed = min(min_speed, self.speed[i])
                if self.brake[i] < BRAKE_OFF:
                    if resume is None and self.throttle[i] >= THROTTLE_RESUME:
                        resume = d
                    if d - onset > ZONE_WINDOW_M or resume is not None:
                        zones.append(BrakingZone(onset, min_speed, resume))
                        braking = False
        if braking:
            zones.append(BrakingZone(onset, min_speed, resume))
        return zones


def _confidence(magnitude: float, threshold: float) -> str:
    return "high" if magnitude >= 2 * threshold else "low"


def compare_zones(current: LapTrace, best: LapTrace,
                  d0: float, d1: float) -> list:
    """Compare every braking zone in [d0, d1] against the reference lap.
    Returns CornerFindings for losses AND gains, empty if inconclusive."""
    if best.max_dist < d1 - 100:
        return []
    cur_zones = current.braking_zones(d0, d1)
    best_zones = best.braking_zones(max(0.0, d0 - 100), d1 + 100)
    findings = []
    for cz in cur_zones:
        match = min(best_zones, key=lambda b: abs(b.onset_m - cz.onset_m),
                    default=None)
        if match is None or abs(match.onset_m - cz.onset_m) > MATCH_TOLERANCE_M:
            continue
        onset_diff = cz.onset_m - match.onset_m
        speed_diff = cz.min_speed - match.min_speed
        resume_diff = (cz.resume_m - match.resume_m
                       if cz.resume_m is not None and match.resume_m is not None
                       else None)
        # Losses
        if speed_diff <= -OVERSLOW_KPH:
            findings.append(CornerFinding(
                match.onset_m, "overslowed", onset_diff, speed_diff,
                confidence=_confidence(-speed_diff, OVERSLOW_KPH)))
        elif onset_diff <= -EARLY_BRAKE_M:
            findings.append(CornerFinding(
                match.onset_m, "braked_early", onset_diff, speed_diff,
                confidence=_confidence(-onset_diff, EARLY_BRAKE_M)))
        elif resume_diff is not None and resume_diff >= LATE_THROTTLE_M:
            findings.append(CornerFinding(
                match.onset_m, "slow_exit", onset_diff, speed_diff,
                confidence=_confidence(resume_diff, LATE_THROTTLE_M)))
        # Gains
        elif speed_diff >= OVERSLOW_KPH:
            findings.append(CornerFinding(
                match.onset_m, "carried_speed", onset_diff, speed_diff, gain=True,
                confidence=_confidence(speed_diff, OVERSLOW_KPH)))
        elif onset_diff >= EARLY_BRAKE_M:
            findings.append(CornerFinding(
                match.onset_m, "later_brakes", onset_diff, speed_diff, gain=True,
                confidence=_confidence(onset_diff, EARLY_BRAKE_M)))
        elif resume_diff is not None and resume_diff <= -LATE_THROTTLE_M:
            findings.append(CornerFinding(
                match.onset_m, "earlier_throttle", onset_diff, speed_diff, gain=True,
                confidence=_confidence(-resume_diff, LATE_THROTTLE_M)))
    return findings


def find_worst_corner(current: LapTrace, best: LapTrace, d0: float, d1: float):
    """The single biggest loss in [d0, d1], or None."""
    losses = [f for f in compare_zones(current, best, d0, d1) if not f.gain]
    return max(losses, key=lambda f: f.score()) if losses else None


class LiveZoneWatcher:
    """Detects braking-zone completion in the live telemetry stream, so a
    corner can be assessed the moment the car exits it — not at the
    sector boundary."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._braking = False
        self._onset = None
        self._min_speed = None
        self._last_d = -1.0

    def feed(self, dist_m: float, speed: int, throttle: float, brake: float):
        """Returns a completed BrakingZone, or None."""
        if dist_m < 0:
            return None
        if dist_m < self._last_d - 200:
            self.reset()  # lap rollover / restart / flashback
        self._last_d = dist_m
        if not self._braking:
            if brake >= BRAKE_ON:
                self._braking = True
                self._onset = dist_m
                self._min_speed = speed
            return None
        self._min_speed = min(self._min_speed, speed)
        resumed = brake < BRAKE_OFF and throttle >= THROTTLE_RESUME
        timed_out = dist_m - self._onset > ZONE_WINDOW_M
        if resumed or timed_out:
            zone = BrakingZone(self._onset, self._min_speed,
                               dist_m if resumed else None)
            self._braking = False
            self._onset = self._min_speed = None
            return zone
        return None


def assess_zone(zone: BrakingZone, reference: LapTrace):
    """Compare one just-completed zone against the reference lap's matching
    zone. Returns a CornerFinding (loss or gain) or None if inconclusive."""
    ref_zones = reference.braking_zones(max(0.0, zone.onset_m - 300),
                                        zone.onset_m + 300)
    match = min(ref_zones, key=lambda b: abs(b.onset_m - zone.onset_m),
                default=None)
    if match is None or abs(match.onset_m - zone.onset_m) > MATCH_TOLERANCE_M:
        return None
    onset_diff = zone.onset_m - match.onset_m
    speed_diff = zone.min_speed - match.min_speed
    resume_diff = (zone.resume_m - match.resume_m
                   if zone.resume_m is not None and match.resume_m is not None
                   else None)
    if speed_diff <= -OVERSLOW_KPH:
        return CornerFinding(match.onset_m, "overslowed", onset_diff, speed_diff,
                             confidence=_confidence(-speed_diff, OVERSLOW_KPH))
    if onset_diff <= -EARLY_BRAKE_M:
        return CornerFinding(match.onset_m, "braked_early", onset_diff, speed_diff,
                             confidence=_confidence(-onset_diff, EARLY_BRAKE_M))
    if resume_diff is not None and resume_diff >= LATE_THROTTLE_M:
        return CornerFinding(match.onset_m, "slow_exit", onset_diff, speed_diff,
                             confidence=_confidence(resume_diff, LATE_THROTTLE_M))
    return None
