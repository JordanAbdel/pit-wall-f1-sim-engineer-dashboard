"""Lap traces — distance-indexed speed/brake/throttle, corner-level comparison.

Every telemetry sample is recorded against lap_distance. When a sector
comes in slower than the session best, the current lap's braking zones
in that sector are matched against the best observed lap's, and the
worst mismatch is classified:

  braked_early — brake onset meaningfully earlier than the best lap
  overslowed   — minimum corner speed well below the best lap's
  slow_exit    — back on full throttle meaningfully later

The game never broadcasts corner names, so zones are named from
TRACK_CORNERS (every numbered turn, from F1 timing data) with a
"the braking zone about N metres into the lap" fallback.
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

# Every numbered turn per track, as (apex_distance_m_from_line,
# turn_number, optional_name). Distances come from F1 timing data (each
# corner's position along a real lap, scaled to the official lap length),
# and numbering follows the current FIA maps for that layout. The game
# never broadcasts corner positions, so this is the only source of them.
# A braking zone is matched to the turn whose apex lies just after it.
# Track ids match packets.TRACK_NAMES; short layouts and circuits without
# data fall back to "the braking zone about N metres into the lap".
CORNER_MATCH_M = 300  # how close a braking zone must be to claim a named turn
BRAKE_LEAD_M = 80     # typical distance from braking onset to the apex

TRACK_CORNERS = {
    0: [  # Melbourne (2022 layout, ~5278 m)
        (360, 1), (440, 2), (1090, 3), (1240, 4), (1440, 5), (1870, 6),
        (1980, 7), (2190, 8), (3310, 9), (3420, 10), (4130, 11),
        (4390, 12), (4640, 13), (4800, 14),
    ],
    1: [  # Paul Ricard (2022 layout, ~5842 m)
        (430, 1), (540, 2), (1120, 3), (1220, 4), (1330, 5), (1460, 6),
        (1680, 7), (2780, 8, "the Mistral chicane"), (2870, 9),
        (3720, 10, "Signes"), (4240, 11, "Beausset"), (4680, 12),
        (4900, 13), (5150, 14), (5320, 15),
    ],
    2: [  # Shanghai (2019 layout, ~5451 m)
        (570, 1), (760, 2), (910, 3), (1050, 4), (1350, 5), (1600, 6),
        (2060, 7), (2360, 8), (2550, 9), (2670, 10), (3150, 11),
        (3230, 12), (3440, 13), (4800, 14, "the hairpin"), (4890, 15),
        (5180, 16),
    ],
    3: [  # Bahrain (2022 layout, ~5412 m)
        (710, 1), (810, 2), (920, 3), (1510, 4), (1780, 5), (1880, 6),
        (1970, 7), (2230, 8), (2590, 9), (2690, 10), (3470, 11),
        (3870, 12), (4090, 13), (4910, 14), (4990, 15),
    ],
    4: [  # Catalunya (2023 layout, ~4657 m)
        (820, 1), (900, 2), (1160, 3), (1730, 4), (2170, 5), (2360, 6),
        (2530, 7), (2620, 8), (2890, 9), (3490, 10), (3640, 11),
        (3750, 12), (4030, 13), (4340, 14),
    ],
    5: [  # Monaco (2022 layout, ~3337 m)
        (200, 1, "Sainte Devote"), (600, 2), (760, 3), (890, 4, "Casino"),
        (1120, 5), (1240, 6, "the hairpin"), (1330, 7),
        (1410, 8, "Portier"), (1750, 9),
        (2090, 10, "the Nouvelle Chicane"), (2140, 11),
        (2370, 12, "Tabac"), (2530, 13, "the Swimming Pool"), (2560, 14),
        (2690, 15), (2720, 16), (2790, 17), (2910, 18, "Rascasse"),
        (3000, 19),
    ],
    6: [  # Montreal (2022 layout, ~4361 m)
        (230, 1), (330, 2), (710, 3), (770, 4), (990, 5), (1210, 6),
        (1300, 7), (1990, 8), (2070, 9), (2670, 10, "the hairpin"),
        (2780, 11), (3150, 12), (3870, 13, "the final chicane"),
        (3900, 14),
    ],
    7: [  # Silverstone (2022 layout, ~5891 m)
        (460, 1, "Abbey"), (620, 2), (870, 3, "Village"),
        (1050, 4, "the Loop"), (1240, 5), (1980, 6, "Brooklands"),
        (2200, 7, "Luffield"), (2550, 8), (3080, 9, "Copse"),
        (3630, 10, "Maggotts"), (3720, 11), (3870, 12), (4010, 13),
        (4160, 14), (5030, 15, "Stowe"), (5490, 16, "Vale"), (5580, 17),
        (5730, 18, "Club"),
    ],
    8: [  # Hockenheim (2019 layout, ~4574 m)
        (270, 1), (840, 2), (900, 3), (970, 4), (1490, 5),
        (2100, 6, "the hairpin"), (2560, 7), (2820, 8), (2930, 9),
        (3000, 10), (3080, 11), (3460, 12), (3810, 13), (3960, 14),
        (4030, 15), (4150, 16), (4290, 17),
    ],
    9: [  # Hungaroring (2021 layout, ~4381 m)
        (650, 1), (840, '1A'), (1150, 2), (1340, 3), (1820, 4), (2060, 5),
        (2400, 6), (2440, 7), (2610, 8), (2750, 9), (2950, 10), (3150, 11),
        (3540, 12), (3670, '12A'), (3810, 13), (4110, 14),
    ],
    10: [  # Spa-Francorchamps (2021 layout, ~7004 m)
        (380, 1, "La Source"), (1070, 2), (1180, 3), (1280, 4),
        (2420, 5, "Les Combes"), (2500, 6), (2670, 7), (3060, 8, "Rivage"),
        (3300, 9), (3820, 10, "Pouhon"), (4070, 11), (4490, 12, "Fagnes"),
        (4650, 13), (4950, 14), (5200, 15, "Stavelot"), (5930, 16),
        (6200, 17), (6740, 18, "the Bus Stop chicane"), (6810, 19),
    ],
    11: [  # Monza (2021 layout, ~5793 m)
        (910, 1, "the Rettifilo chicane"), (970, 2), (1490, 3),
        (2140, 4, "the Roggia chicane"), (2190, 5),
        (2550, 6, "the first Lesmo"), (2870, 7, "the second Lesmo"),
        (3950, 8, "Ascari"), (4040, 9), (4140, 10),
        (5230, 11, "Parabolica"),
    ],
    12: [  # Singapore (2023 layout, ~4940 m)
        (410, 1), (470, 2), (550, 3), (700, 4), (970, 5), (1400, 6),
        (1780, 7), (2010, 8), (2200, 9), (2630, 10), (2790, 11),
        (2880, 12), (3040, 13), (3610, 14), (3840, 15), (4330, 16),
        (4410, 17), (4680, 18), (4800, 19),
    ],
    13: [  # Suzuka (2025 layout, ~5807 m)
        (710, 1), (820, 2), (1070, 3), (1200, 4), (1360, 5), (1520, 6),
        (1860, 7), (2270, 8, "Degner"), (2430, 9), (2750, 10),
        (2900, 11, "the hairpin"), (3150, 12), (3790, 13, "Spoon"),
        (3950, 14), (4990, 15), (5390, 16, "the Casio chicane"),
        (5460, 17), (5630, 18),
    ],
    14: [  # Abu Dhabi (2021 layout, ~5281 m)
        (390, 1), (640, 2), (850, 3), (1040, 4), (1440, 5, "the hairpin"),
        (2650, 6), (2710, 7), (2910, 8), (3740, 9), (4080, 10), (4240, 11),
        (4370, 12), (4480, 13), (4600, 14), (4870, 15), (5110, 16),
    ],
    15: [  # Circuit Of The Americas (2021 layout, ~5513 m)
        (650, 1), (900, 2), (1170, 3), (1250, 4), (1350, 5), (1490, 6),
        (1720, 7), (1880, 8), (1970, 9), (2180, 10), (2570, 11),
        (3780, 12), (4020, 13), (4090, 14), (4300, 15), (4490, 16),
        (4620, 17), (4760, 18), (5050, 19), (5360, 20),
    ],
    16: [  # Interlagos (2023 layout, ~4309 m)
        (350, 1, "the Senna S"), (450, 2), (640, 3),
        (1410, 4, "Descida do Lago"), (1570, 5), (2020, 6), (2160, 7),
        (2330, 8), (2450, 9), (2750, 10), (2960, 11), (3260, 12, "Juncao"),
        (3370, 13), (3640, 14), (4040, 15),
    ],
    17: [  # Red Bull Ring (2022 layout, ~4318 m)
        (430, 1), (1070, 2), (1380, 3, "Remus"), (2190, 4, "Schlossgold"),
        (2380, 5), (2710, 6), (3040, 7), (3240, 8), (3780, 9), (3970, 10),
    ],
    18: [  # Sochi (2021 layout, ~5848 m)
        (430, 1), (1200, 2), (1650, 3), (2060, 4), (2510, 5), (2770, 6),
        (2940, 7), (3230, 8), (3350, 9), (3610, 10), (4060, 11),
        (4530, 12), (4750, 13), (4870, 14), (5140, 15), (5240, 16),
        (5590, 17), (5760, 18),
    ],
    19: [  # Mexico City (2021 layout, ~4304 m)
        (1220, 1), (1300, 2), (1360, 3), (2060, 4), (2130, 5), (2270, 6),
        (2610, 7), (2730, 8), (2800, 9), (2990, 10), (3090, 11),
        (3660, 12), (3840, 13), (3910, 14), (3950, 15), (4020, 16),
        (4140, 17),
    ],
    20: [  # Baku (2022 layout, ~6003 m)
        (300, 1), (640, 2), (1500, 3), (1730, 4), (2060, 5), (2120, 6),
        (2510, 7), (2710, 8), (2740, 9), (2760, 10), (2810, 11),
        (2870, 12), (3270, 13), (3500, 14), (3720, 15), (4080, 16),
        (4270, 17), (4510, 18), (4670, 19), (4980, 20),
    ],
    26: [  # Zandvoort (2021 layout, ~4259 m)
        (360, 1, "Tarzan"), (670, 2), (840, 3, "Hugenholtz"), (1050, 4),
        (1240, 5), (1400, 6), (1710, 7), (2030, 8), (2250, 9), (2510, 10),
        (3090, 11), (3180, 12), (3490, 13), (3770, 14),
    ],
    27: [  # Imola (2022 layout, ~4909 m)
        (260, 1), (890, 2, "Tamburello"), (950, 3), (1100, 4),
        (1530, 5, "Villeneuve"), (1630, 6), (1910, 7, "Tosa"), (2310, 8),
        (2560, 9, "Piratella"), (2700, 10), (2950, 11, "Acque Minerali"),
        (3040, 12), (3130, 13), (3540, 14, "Variante Alta"), (3590, 15),
        (4150, 16), (4350, 17, "Rivazza"), (4490, 18), (4800, 19),
    ],
    28: [  # Portimão (2021 layout, ~4653 m)
        (670, 1), (830, 2), (980, 3), (1130, 4), (1710, 5), (1940, 6),
        (2180, 7), (2340, 8), (2680, 9), (2940, 10), (3000, 11),
        (3220, 12), (3420, 13), (3650, 14), (4100, 15),
    ],
    29: [  # Jeddah (2022 layout, ~6174 m)
        (490, 1), (550, 2), (690, 3), (1000, 4), (1100, 5), (1260, 6),
        (1380, 7), (1450, 8), (1610, 9), (1710, 10), (1810, 11),
        (1900, 12), (2440, 13), (2760, 14), (2960, 15), (3090, 16),
        (3200, 17), (3420, 18), (3620, 19), (3830, 20), (4030, 21),
        (4280, 22), (4370, 23), (4510, 24), (4810, 25), (5170, 26),
        (5560, 27, "the final hairpin"),
    ],
    30: [  # Miami (2022 layout, ~5412 m)
        (380, 1), (490, 2), (580, 3), (1150, 4), (1290, 5), (1440, 6),
        (1580, 7), (1680, 8), (2210, 9), (2500, 10), (3140, 11),
        (3260, 12), (3390, 13), (3460, 14), (3480, 15), (3570, 16),
        (4910, 17), (5050, 18), (5260, 19),
    ],
    31: [  # Las Vegas (2023 layout, ~6201 m)
        (280, 1), (380, 2), (570, 3), (690, 4), (1550, 5), (1820, 6),
        (2040, 7), (2090, 8), (2220, 9), (2710, 10), (2980, 11),
        (3210, 12), (3970, 13), (5160, 14), (5220, 15), (5280, 16),
        (6040, 17),
    ],
    32: [  # Losail (2023 layout, ~5419 m)
        (800, 1), (1090, 2), (1320, 3), (1740, 4), (1900, 5), (2210, 6),
        (2610, 7), (2800, 8), (2930, 9), (3160, 10), (3470, 11),
        (3780, 12), (3980, 13), (4190, 14), (4490, 15), (4960, 16),
    ],
}

# Older layouts the earlier games still use, keyed by their lap length so
# the Session packet's track length picks the right one (see
# set_track_length). Turn numbers differ between layouts.
LAYOUT_VARIANTS = {
    4: [(4675, [  # with the final chicane, F1 2020-22
        (830, 1), (910, 2), (1170, 3), (1730, 4), (2110, 5), (2370, 6),
        (2540, 7), (2630, 8), (2920, 9), (3500, 10), (3640, 11),
        (3780, 12), (4020, 13), (4150, 14), (4190, 15), (4360, 16),
    ])],
    12: [(5063, [  # pre-2023 layout, F1 2020-23
        (410, 1), (480, 2), (570, 3), (680, 4), (970, 5), (1450, 6),
        (1790, 7), (2010, 8), (2200, 9), (2630, 10), (2780, 11),
        (2860, 12), (3050, 13), (3580, 14), (3840, 15), (3940, 16),
        (4030, 17), (4240, 18), (4330, 19), (4470, 20), (4540, 21),
        (4830, 22), (4940, 23),
    ])],
    14: [(5554, [  # pre-2021 layout, F1 2020
        (400, 1), (640, 2), (850, 3), (1040, 4), (1310, 5, "the hairpin"),
        (1380, 6), (1520, 7), (2760, 8), (2840, 9), (3110, 10), (3790, 11),
        (3850, 12), (3900, 13), (4030, 14), (4320, 15), (4490, 16),
        (4610, 17), (4730, 18), (4850, 19), (5160, 20), (5370, 21),
    ])],
}

# Spoken turn numbers 1-20; above that TTS reads the numeral fine.
_NUM_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
              6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
              11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen",
              15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
              19: "nineteen", 20: "twenty"}


_track_length = {}


def set_track_length(track_id, length_m) -> None:
    """Remember the lap length the game reports, so a track with several
    layouts (Singapore before and after 2023, say) names the right turns."""
    if track_id is not None and length_m:
        _track_length[track_id] = length_m


# Lap length of the TRACK_CORNERS layout, for tracks that have variants.
CURRENT_LENGTH_M = {4: 4657, 12: 4940, 14: 5281}


def _corners(track_id):
    """The layout whose lap length is closest to what the game reports."""
    length = _track_length.get(track_id)
    best = TRACK_CORNERS.get(track_id, ())
    if length and track_id in CURRENT_LENGTH_M:
        gap = abs(length - CURRENT_LENGTH_M[track_id])
        for nominal, corners in LAYOUT_VARIANTS.get(track_id, ()):
            if abs(length - nominal) < gap:
                best, gap = corners, abs(length - nominal)
    return best


def _corner_at(track_id, onset_m: float):
    """Nearest listed corner within tolerance, or None. The table holds
    apexes, so a braking onset is moved forward to where its apex is."""
    best, apex = None, onset_m + BRAKE_LEAD_M
    for entry in _corners(track_id):
        d = abs(apex - entry[0])
        if d <= CORNER_MATCH_M and (best is None or d < abs(apex - best[0])):
            best = entry
    return best


def turn_name(track_id, onset_m: float) -> str:
    """Spoken form: 'turn ten', or 'turn ten, Pouhon' when a name is set."""
    c = _corner_at(track_id, onset_m)
    if c is None:
        return f"the braking zone {_distance_words(onset_m)} into the lap"
    num = c[1]
    base = f"turn {_NUM_WORDS.get(num, num)}"
    name = c[2] if len(c) > 2 else None
    return f"{base}, {name}" if name else base


def _distance_words(m: float) -> str:
    """How an engineer says a lap distance: 'about 300 metres', 'about
    2.3 kilometres' — never a bare 'k'."""
    if m < 950:
        return f"about {max(100, round(m / 100) * 100)} metres"
    km = round(m / 1000, 1)
    return f"about {km:g} kilometre{'' if km == 1 else 's'}"


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
        self.time = []           # elapsed lap ms per sample (None if unknown)
        self.lap_time_ms = None

    def add(self, dist_m: float, speed: int, throttle: float, brake: float,
            time_ms: int | None = None) -> None:
        if dist_m < 0:
            return
        if self.dist and dist_m < self.dist[-1]:
            return  # flashback rewind — skip until distance recovers
        self.dist.append(dist_m)
        self.speed.append(speed)
        self.throttle.append(throttle)
        self.brake.append(brake)
        self.time.append(time_ms)

    def compact(self, step_m: float = 10.0) -> dict | None:
        """The trace thinned to one sample per step_m, for the dashboard's
        telemetry overlay and its distance-based live delta. Throttle and
        brake are rounded to whole percent to keep the JSON small."""
        if not self.dist:
            return None
        out = {"dist": [], "speed": [], "throttle": [], "brake": [],
               "time": [], "lapMs": self.lap_time_ms}
        next_d = -1.0
        for i, d in enumerate(self.dist):
            if d < next_d:
                continue
            next_d = d + step_m
            out["dist"].append(round(d, 1))
            out["speed"].append(int(self.speed[i]))
            out["throttle"].append(round(self.throttle[i] * 100))
            out["brake"].append(round(self.brake[i] * 100))
            out["time"].append(self.time[i])
        return out

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
