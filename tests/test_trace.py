"""Lap traces — braking-zone detection and corner-level comparison."""
import trace
from trace import LapTrace, LiveZoneWatcher


def build_trace(onset_m, min_speed, resume_after_m=80, top=300, length=2500):
    """One clean braking zone: flat out, brake to min_speed, coast, then
    back to full throttle at onset + resume_after_m."""
    t = LapTrace()
    d = 0.0
    while d < length:
        if d < onset_m:
            t.add(d, top, 1.0, 0.0)
        elif d < onset_m + 40:
            t.add(d, min_speed, 0.0, 0.9)          # hard braking
        elif d < onset_m + resume_after_m:
            t.add(d, min_speed, 0.3, 0.0)          # coasting through apex
        else:
            t.add(d, top, 1.0, 0.0)                # power down
        d += 5.0
    return t


def test_braking_zone_detection():
    t = build_trace(1000, 120, resume_after_m=80)
    zones = t.braking_zones(0, 2500)
    assert len(zones) == 1
    z = zones[0]
    assert abs(z.onset_m - 1000) <= 5
    assert z.min_speed == 120
    assert z.resume_m is not None and abs(z.resume_m - 1080) <= 5


def test_flashback_rewind_samples_are_dropped():
    t = LapTrace()
    t.add(100, 300, 1.0, 0.0)
    t.add(50, 200, 1.0, 0.0)    # distance went backwards — ignored
    assert t.dist == [100]


def cause_of(current, best):
    findings = trace.compare_zones(current, best, 0, 2500)
    assert findings, "expected a finding"
    return findings[0]


def test_braked_early_and_late():
    best = build_trace(1000, 120)
    f = cause_of(build_trace(950, 120), best)      # 50 m early
    assert f.cause == "braked_early" and not f.gain
    assert f.confidence == "high"                  # 50 >= 2 * 15
    # Braking 50 m later while getting on the power at the same point —
    # resume shifted so both laps power down at 1080 m.
    g = cause_of(build_trace(1050, 120, resume_after_m=30), best)
    assert g.cause == "later_brakes" and g.gain


def test_overslowed_and_carried_speed():
    best = build_trace(1000, 120)
    f = cause_of(build_trace(1000, 108), best)     # 12 kph down at apex
    assert f.cause == "overslowed" and not f.gain
    assert f.confidence == "high"                  # 12 >= 2 * 6
    weak = cause_of(build_trace(1000, 113), best)  # 7 kph — real but marginal
    assert weak.cause == "overslowed" and weak.confidence == "low"
    g = cause_of(build_trace(1000, 132), best)
    assert g.cause == "carried_speed" and g.gain


def test_slow_exit_and_earlier_throttle():
    best = build_trace(1000, 120, resume_after_m=80)
    f = cause_of(build_trace(1000, 120, resume_after_m=120), best)
    assert f.cause == "slow_exit" and not f.gain
    g = cause_of(build_trace(1000, 120, resume_after_m=50), best)
    assert g.cause == "earlier_throttle" and g.gain


def test_incomplete_reference_returns_nothing():
    best = build_trace(1000, 120, length=1200)     # reference stops early
    assert trace.compare_zones(build_trace(950, 120), best, 0, 2500) == []


def test_live_zone_watcher_and_assess():
    best = build_trace(1000, 120)
    w = LiveZoneWatcher()
    zone = None
    cur = build_trace(950, 120)
    for i in range(len(cur.dist)):
        z = w.feed(cur.dist[i], cur.speed[i], cur.throttle[i], cur.brake[i])
        if z is not None:
            zone = z
    assert zone is not None and abs(zone.onset_m - 950) <= 5
    finding = trace.assess_zone(zone, best)
    assert finding is not None and finding.cause == "braked_early"


def test_turn_names():
    assert trace.turn_name(10, 3740) == "turn ten, Pouhon"     # Spa
    assert trace.turn_label(10, 3740) == "T10"
    assert trace.turn_name(7, 4930) == "turn fifteen, Stowe"   # Silverstone
    assert trace.turn_name(5, 1170) == "turn six, the hairpin"  # Monaco
    assert "braking zone" in trace.turn_name(10, 5500)          # unmapped gap
    assert trace.turn_label(99, 2100) == "2.1k"                 # unknown track


def test_compact_thins_by_distance_and_keeps_time():
    t = LapTrace()
    for i in range(400):                       # a sample every 2.5 m
        t.add(i * 2.5, 200 + i % 7, 0.5, 0.25, time_ms=i * 40)
    t.lap_time_ms = 16_000
    c = t.compact(step_m=10.0)
    assert c["lapMs"] == 16_000
    assert len(c["dist"]) == 100
    assert all(b - a >= 10.0 for a, b in zip(c["dist"], c["dist"][1:]))
    assert c["throttle"][0] == 50 and c["brake"][0] == 25   # whole percent
    assert c["time"][:3] == [0, 160, 320]
    assert len({len(c[k]) for k in ("dist", "speed", "throttle", "brake", "time")}) == 1


def test_compact_empty_and_untimed():
    assert LapTrace().compact() is None
    t = LapTrace()
    t.add(0, 100, 1.0, 0.0)                    # time is optional
    assert t.compact()["time"] == [None]


def test_older_layouts_are_picked_by_lap_length():
    # Singapore: turn 18 sits ~4.24 km in on the old layout and ~4.68 km
    # on the 2023 one, so the reported length decides which turn it is.
    trace.set_track_length(12, 5063)
    assert trace.turn_label(12, 4160) == "T18"
    trace.set_track_length(12, 4940)
    assert trace.turn_label(12, 4600) == "T18"
    assert trace.turn_label(12, 4160) != "T18"
    # Catalunya's two layouts are only 18 m apart.
    trace.set_track_length(4, 4657)
    assert trace._corners(4) is trace.TRACK_CORNERS[4]
    trace.set_track_length(4, 4675)
    assert trace._corners(4) is trace.LAYOUT_VARIANTS[4][0][1]
