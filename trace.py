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

# Major heavy-braking corners per track, as (distance_m_from_line,
# turn_number, optional_name). Turn numbers are the reliable unit; the
# distances are best-effort estimates anchored to real circuit layouts
# (the game never broadcasts corner positions), so a call may occasionally
# land on a neighbouring turn — corrected as they're noticed. Only the
# well-separated heavy-braking zones the detector actually flags are
# listed; ambiguous closely-spaced chicane pairs are left to the generic
# "braking zone X.X k in" fallback. Track ids match packets.TRACK_NAMES.
CORNER_MATCH_M = 300  # how close a braking zone must be to claim a named turn

TRACK_CORNERS = {
    0: [  # Melbourne — Albert Park (~5278 m)
        (330, 1), (880, 3), (1800, 6), (3150, 11), (3950, 13),
    ],
    2: [  # Shanghai (~5451 m)
        (650, 1), (1950, 6), (3350, 11), (4950, 14),
    ],
    3: [  # Sakhir — Bahrain (~5412 m)
        (1050, 1), (1800, 4), (2700, 8), (3350, 10), (3750, 11), (4450, 13),
    ],
    4: [  # Catalunya (~4657 m)
        (900, 1), (1550, 4), (1950, 5), (2500, 7), (3350, 10), (4050, 13),
    ],
    5: [  # Monaco (~3337 m)
        (180, 1, "Sainte Devote"), (1050, 6, "the hairpin"),
        (1750, 10, "the Nouvelle Chicane"), (2950, 18, "Rascasse"),
    ],
    6: [  # Montreal (~4361 m)
        (180, 2), (1150, 6), (1500, 8), (2450, 10, "the hairpin"), (3900, 13),
    ],
    7: [  # Silverstone (~5891 m)
        (900, 3, "Village"), (1600, 6, "Brooklands"), (1950, 7, "Luffield"),
        (3950, 15, "Stowe"), (4350, 16, "Vale"),
    ],
    9: [  # Hungaroring (~4381 m)
        (600, 1), (950, 2), (1550, 4), (3700, 12),
    ],
    10: [  # Spa-Francorchamps (~7004 m)
        (340, 1, "La Source"), (2050, 5, "Les Combes"), (2750, 8, "Rivage"),
        (3390, 10, "Pouhon"), (4150, 12, "Fagnes"), (4900, 15, "Stavelot"),
        (6580, 19, "the Bus Stop chicane"),
    ],
    11: [  # Monza (~5793 m)
        (650, 1, "the Rettifilo chicane"), (1450, 4, "the Roggia chicane"),
        (1900, 6, "Lesmo"), (3750, 8, "Ascari"), (5150, 11, "Parabolica"),
    ],
    12: [  # Singapore — Marina Bay (~4940 m)
        (350, 1), (1900, 7), (3100, 13), (3900, 16),
    ],
    13: [  # Suzuka (~5807 m)
        (700, 1), (2100, 8, "Degner"), (2900, 11, "the hairpin"),
        (3900, 13, "Spoon"), (5450, 16, "the Casio chicane"),
    ],
    14: [  # Abu Dhabi — Yas Marina (~5281 m)
        (350, 1), (1350, 5, "the hairpin"), (2900, 9),
    ],
    15: [  # Texas — COTA (~5513 m)
        (700, 1), (2500, 11), (3400, 12, "the hairpin"), (4350, 15), (5250, 20),
    ],
    16: [  # Brazil — Interlagos (~4309 m)
        (500, 1, "the Senna S"), (1300, 4), (2200, 8), (2900, 10),
        (3500, 12, "Juncao"),
    ],
    17: [  # Austria — Red Bull Ring (~4318 m)
        (600, 1), (1300, 3), (2050, 4), (2900, 6), (3900, 9),
    ],
    19: [  # Mexico City (~4304 m)
        (850, 1), (1500, 4), (2150, 6), (3400, 12),
    ],
    20: [  # Baku (~6003 m)
        (250, 1), (900, 3), (1450, 4), (4350, 15), (4650, 16),
    ],
    26: [  # Zandvoort (~4259 m)
        (450, 1, "Tarzan"), (900, 3), (1900, 7), (2900, 11),
    ],
    27: [  # Imola (~4909 m)
        (500, 2, "Tamburello"), (1400, 5, "Tosa"), (2600, 9, "Acque Minerali"),
        (3250, 12, "Variante Alta"), (4050, 14, "Rivazza"),
    ],
    29: [  # Jeddah (~6174 m)
        (1050, 1), (2900, 13), (5000, 22), (6000, 27),
    ],
    30: [  # Miami (~5412 m)
        (900, 1), (2100, 8), (2900, 11), (4900, 17),
    ],
    31: [  # Las Vegas (~6201 m)
        (800, 1), (1600, 5), (3300, 12), (5200, 14),
    ],
    32: [  # Qatar — Losail (~5419 m)
        (600, 1), (2000, 6), (3100, 10), (5000, 16),
    ],
}

# Spoken turn numbers 1-20; above that TTS reads the numeral fine.
_NUM_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
              6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
              11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
              15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
              19: "nineteen", 20: "twenty"}


def _corner_at(track_id, onset_m: float):
    """Nearest listed corner within tolerance, or None."""
    best = None
    for entry in TRACK_CORNERS.get(track_id, ()):
        d = abs(onset_m - entry[0])
        if d <= CORNER_MATCH_M and (best is None or d < abs(onset_m - best[0])):
            best = entry
    return best


def turn_name(track_id, onset_m: float) -> str:
    """Spoken form: 'turn ten', or 'turn ten, Pouhon' when a name is set."""
    c = _corner_at(track_id, onset_m)
    if c is None:
        return f"the braking zone {onset_m / 1000:.1f} k in"
    num = c[1]
    base = f"turn {_NUM_WORDS.get(num, num)}"
    name = c[2] if len(c) > 2 else None
    return f"{base}, {name}" if name else base


def turn_label(track_id, onset_m: float) -> str:
    """Short UI label: 'T10' or '2.1k'."""
    c = _corner_at(track_id, onset_m)
    return f"T{c[1]}" if c is not None else f"{onset_m / 1000:.1f}k"


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
