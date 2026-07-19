"""Analyzer — bests, deltas, stint pace, and the session recap."""
from analysis import Analyzer, session_recap
from state import LapComplete, SectorComplete


def sector(lap, n, ms, invalid=False):
    return SectorComplete(lap, n, ms, invalid)


def test_sector_bests_and_deltas():
    a = Analyzer()
    r1 = a.on_sector(sector(1, 1, 31_200))
    assert r1.delta_ms is None and r1.is_best
    r2 = a.on_sector(sector(2, 1, 30_900))
    assert r2.delta_ms == -300 and r2.is_best
    r3 = a.on_sector(sector(3, 1, 31_400))
    assert r3.delta_ms == 500 and not r3.is_best
    assert a.best_sectors[1] == 30_900


def test_invalid_sector_never_becomes_best():
    a = Analyzer()
    a.on_sector(sector(1, 1, 31_200))
    r = a.on_sector(sector(2, 1, 29_000, invalid=True))
    assert r.delta_ms == -2_200 and not r.is_best
    assert a.best_sectors[1] == 31_200


def test_lap_best_and_worst_sector():
    a = Analyzer()
    for n, ms in ((1, 31_200), (2, 42_800), (3, 28_400)):
        a.on_sector(sector(1, n, ms))
    a.on_lap(LapComplete(1, 102_400, (31_200, 42_800, 28_400), False))
    # lap 2: loses most time in sector 2
    a.on_sector(sector(2, 1, 31_300))
    a.on_sector(sector(2, 2, 43_600))
    a.on_sector(sector(2, 3, 28_500))
    r = a.on_lap(LapComplete(2, 103_400, (31_300, 43_600, 28_500), False))
    assert r.delta_ms == 1_000 and not r.is_best
    assert r.worst_sector == 2


def test_seed_bests_only_lowers():
    a = Analyzer()
    a.seed_bests(31_000, 42_000, 28_000, 101_000)
    a.seed_bests(32_000, 41_000, 0, 105_000)   # worse/missing values ignored
    assert a.best_sectors == {1: 31_000, 2: 41_000, 3: 28_000}
    assert a.best_lap_ms == 101_000


def test_stint_delta_excludes_outliers():
    a = Analyzer()
    times = [101_000, 101_200, 101_100]
    for i, t in enumerate(times, start=1):
        a.on_lap(LapComplete(i, t, (0, 0, 0), False))
    spin = a.on_lap(LapComplete(4, 109_000, (0, 0, 0), False))
    assert spin.stint_delta_ms >= 5_000       # flagged as an off
    nxt = a.on_lap(LapComplete(5, 101_150, (0, 0, 0), False))
    # the spin lap was not absorbed into the reference median
    assert abs(nxt.stint_delta_ms) < 200


def test_session_recap():
    laps = [{"lap": i + 1, "sectors": s} for i, s in enumerate([
        [31_200, 42_800, 28_400],
        [30_900, 43_100, 28_100],
        [31_400, 42_600, 28_600],
        [30_600, 42_400, 27_900],
    ])]
    r = session_recap(laps)
    assert r["laps"] == 4
    assert r["best_lap_ms"] == 100_900
    assert r["theoretical_ms"] == 30_600 + 42_400 + 27_900
    assert r["weakest_sector"] in (1, 2, 3)
    assert r["consistency_ms"] is not None
    assert session_recap([]) is None
