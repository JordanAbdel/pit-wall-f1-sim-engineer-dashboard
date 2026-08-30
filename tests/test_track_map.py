"""Track outlines recorded from Motion-packet world positions."""
import math

import pytest
import track_map


def oval_lap(n=400, rx=900.0, rz=400.0):
    """A closed loop in world metres, sampled evenly by distance."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((rx * math.cos(a), rz * math.sin(a)))
    return pts


def test_recorder_samples_by_distance_not_by_packet():
    """Motion arrives up to 60x a second; without distance gating a slow lap
    would bury the path in duplicate points."""
    rec = track_map.MapRecorder()
    for i in range(500):
        rec.add(i * 1.0, float(i), 0.0)      # 1 m apart, 500 m of lap
    # SAMPLE_M is 15 m, so ~500/15 points, not 500
    assert 30 <= len(rec.points) <= 36


def test_recorder_ignores_a_stationary_or_reversing_car():
    rec = track_map.MapRecorder()
    rec.add(100.0, 0.0, 0.0)
    before = len(rec.points)
    for _ in range(50):
        rec.add(100.0, 5.0, 5.0)            # parked
    rec.add(20.0, 9.0, 9.0)                 # went backwards (flashback)
    rec.add(-1.0, 9.0, 9.0)                 # pre-line negative distance
    assert len(rec.points) == before


def test_recorder_needs_a_real_lap_before_it_is_usable():
    rec = track_map.MapRecorder()
    for i in range(10):
        rec.add(i * 20.0, float(i), 0.0)
    assert not rec.usable
    for i in range(10, 100):
        rec.add(i * 20.0, float(i), 0.0)
    assert rec.usable


def test_path_fits_the_viewbox_and_keeps_aspect_ratio():
    entry = track_map.to_path(oval_lap())
    assert entry is not None
    nums = [float(t) for t in entry["path"]
            .replace("M", " ").replace("L", " ").replace("Z", " ").split()]
    xs, ys = nums[0::2], nums[1::2]
    assert min(xs) >= 0 and max(xs) <= track_map.VIEW_W
    assert min(ys) >= 0 and max(ys) <= track_map.VIEW_H
    # a 900x400 oval is wider than it is tall; the drawing must be too
    assert (max(xs) - min(xs)) > (max(ys) - min(ys))
    ratio = (max(xs) - min(xs)) / (max(ys) - min(ys))
    assert 2.1 <= ratio <= 2.4          # 900/400 = 2.25, undistorted
    assert entry["path"].endswith("Z")  # closed loop


def test_too_few_points_produces_no_map():
    assert track_map.to_path([(0.0, 0.0), (1.0, 1.0)]) is None


def test_a_flat_line_is_rejected_rather_than_dividing_by_zero():
    assert track_map.to_path([(float(i), 0.0) for i in range(200)]) is None


def test_first_recorded_lap_wins_and_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(track_map, "STORE", tmp_path / "maps.json")
    maps = {}
    first = track_map.record(maps, 0, oval_lap(), track_length=5278)
    assert first is not None and "0" in maps
    assert first["trackLength"] == 5278

    # a later, differently-shaped lap must not replace it
    again = track_map.record(maps, 0, oval_lap(rx=100.0, rz=100.0))
    assert again["path"] == first["path"]

    reloaded = __import__("json").loads((tmp_path / "maps.json").read_text())
    assert reloaded["0"]["path"] == first["path"]


def test_unknown_track_is_not_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(track_map, "STORE", tmp_path / "maps.json")
    assert track_map.record({}, None, oval_lap()) is None


# --- completeness: the failure that drew a sliver instead of a circuit ------

def full_lap_samples(length=5278.0, step=15.0):
    """A clean lap: evenly spaced from the line all the way round."""
    rec = track_map.MapRecorder()
    n = int(length / step)
    for i in range(n):
        d = i * step
        a = 2 * math.pi * d / length
        rec.add(d, 900 * math.cos(a), 420 * math.sin(a))
    return rec


def test_a_clean_lap_is_accepted():
    rec = full_lap_samples()
    assert rec.complete(5278)
    assert rec.gaps() <= track_map.MAX_GAP_M


def test_a_time_trial_restart_mid_lap_is_rejected():
    """The real bug: distance rewinds on a restart, later points are refused
    until the car passes the old distance, and the fragments get stitched
    into a path that teleports across the circuit."""
    rec = track_map.MapRecorder()
    for i in range(40):                      # first attempt, 0 -> 600 m
        rec.add(i * 15.0, float(i), 0.0)
    # driver restarts; distance rewinds. add() refuses everything below the
    # high-water mark, so the next accepted sample is far around the lap.
    for i in range(40):
        rec.add(i * 15.0, -float(i), 5.0)    # all ignored
    for i in range(40, 200):                 # resumes much later in the lap
        rec.add(i * 15.0 + 2000, float(i), 1.0)

    assert rec.gaps() > track_map.MAX_GAP_M
    assert not rec.complete(5278)


def test_a_lap_joined_partway_round_is_rejected():
    rec = track_map.MapRecorder()
    for i in range(200):
        rec.add(2000.0 + i * 15.0, float(i), float(i))
    assert rec.samples[0][0] > track_map.START_WITHIN_M
    assert not rec.complete(5278)


def test_a_lap_that_stops_short_is_rejected():
    """63 points covering ~18% of Albert Park is what produced the sliver."""
    rec = track_map.MapRecorder()
    for i in range(63):
        rec.add(i * 15.0, float(i), float(i))
    assert not rec.complete(5278)           # only ~945 m of 5278 m


def test_coverage_is_measured_against_the_real_track_length():
    rec = full_lap_samples(length=5278.0)
    assert rec.complete(5278)
    # the same lap is not a complete recording of a much longer circuit
    assert not rec.complete(7004)           # Spa


def test_reset_clears_the_high_water_mark_so_a_new_attempt_records():
    rec = track_map.MapRecorder()
    for i in range(40):
        rec.add(i * 15.0, float(i), 0.0)
    rec.reset()
    assert rec.samples == []
    rec.add(0.0, 1.0, 1.0)                  # would be refused without a reset
    assert len(rec.samples) == 1
