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


# --- provisional baselines from invalid laps --------------------------------

def _sector(analyzer, lap, sector, ms, invalid):
    from state import SectorComplete
    return analyzer.on_sector(
        SectorComplete(lap_num=lap, sector=sector, time_ms=ms,
                       lap_invalid=invalid, stats=None))


def test_invalid_lap_seeds_a_baseline_when_nothing_valid_exists():
    """Time Trial deletes most laps for track limits. Without this, a sector
    the driver always runs wide in never gets a baseline, so delta stays None
    and that sector is never coached."""
    a = Analyzer()
    r1 = _sector(a, 1, 3, 36_000, invalid=True)
    assert r1.delta_ms is None          # nothing to compare the first one to
    assert not r1.is_best               # an invalid lap is never a "personal best"
    assert a.best_sectors[3] == 36_000  # ...but it does seed the baseline
    assert not a.best_sector_valid[3]

    r2 = _sector(a, 2, 3, 36_500, invalid=True)
    assert r2.delta_ms == 500           # coaching now has something to say


def test_a_valid_time_always_replaces_a_provisional_one_even_if_slower():
    a = Analyzer()
    _sector(a, 1, 1, 28_000, invalid=True)     # provisional, possibly a cut
    assert a.best_sectors[1] == 28_000 and not a.best_sector_valid[1]

    _sector(a, 2, 1, 28_400, invalid=False)    # slower, but legitimate
    assert a.best_sectors[1] == 28_400
    assert a.best_sector_valid[1]

    # once valid, a slower valid lap must not replace it
    _sector(a, 3, 1, 28_900, invalid=False)
    assert a.best_sectors[1] == 28_400
    # and an invalid lap can no longer take over, however quick
    _sector(a, 4, 1, 27_000, invalid=True)
    assert a.best_sectors[1] == 28_400


def test_faster_invalid_lap_still_improves_a_provisional_baseline():
    a = Analyzer()
    _sector(a, 1, 2, 19_500, invalid=True)
    _sector(a, 2, 2, 19_100, invalid=True)
    assert a.best_sectors[2] == 19_100
    assert not a.best_sector_valid[2]


# --- deleted laps never count as a best -------------------------------------

def _lap(n, sectors, invalid=False):
    return {"lap": n, "sectors": sectors, "invalid": invalid}


def test_recap_ignores_a_faster_deleted_lap():
    laps = [_lap(1, [31_000, 42_800, 28_400]),
            _lap(2, [30_500, 42_600, 28_000], invalid=True),  # "lap deleted"
            _lap(3, [31_100, 42_900, 28_300])]
    r = session_recap(laps)
    assert r["best_lap_ms"] == 31_000 + 42_800 + 28_400
    assert r["theoretical_ms"] == 31_000 + 42_800 + 28_300
    assert r["best_lap_valid"]
    assert r["laps"] == 3  # still counted as driven


def test_recap_falls_back_to_deleted_laps_when_nothing_is_valid():
    laps = [_lap(1, [31_000, 42_800, 28_400], invalid=True),
            _lap(2, [30_500, 42_600, 28_000], invalid=True)]
    r = session_recap(laps)
    assert r["best_lap_ms"] == 30_500 + 42_600 + 28_000
    assert not r["best_lap_valid"]


def test_lap_deleted_after_s2_gives_back_its_sector_bests():
    """The log's "best S2 mismatch: game says 42.800, we tracked 42.600":
    S2 closed clean, then the lap was deleted in S3. The game drops the
    sector; so must we."""
    a = Analyzer()
    a.on_sector(sector(1, 1, 31_000))
    a.on_sector(sector(1, 2, 42_800))
    a.on_sector(sector(1, 3, 28_400))
    a.on_lap(LapComplete(1, 102_200, (31_000, 42_800, 28_400), False))

    a.on_sector(sector(2, 1, 30_900))
    a.on_sector(sector(2, 2, 42_600))              # valid when it closed
    a.on_sector(sector(2, 3, 28_000, invalid=True))  # cut in S3: lap deleted
    a.on_lap(LapComplete(2, 101_500, (30_900, 42_600, 28_000), True))

    assert a.best_sectors == {1: 31_000, 2: 42_800, 3: 28_400}
    assert all(a.best_sector_valid.values())
    assert a.best_lap_ms == 102_200


def test_deleted_first_lap_stays_a_provisional_baseline():
    a = Analyzer()
    a.on_sector(sector(1, 1, 31_000))
    a.on_sector(sector(1, 2, 42_600))
    a.on_sector(sector(1, 3, 28_000, invalid=True))
    a.on_lap(LapComplete(1, 101_600, (31_000, 42_600, 28_000), True))
    # still a baseline to coach against, but a valid time replaces it
    assert a.best_sectors[2] == 42_600 and not a.best_sector_valid[2]
    a.on_sector(sector(2, 2, 42_900))
    assert a.best_sectors[2] == 42_900 and a.best_sector_valid[2]


def test_seeded_bests_are_valid_and_outrank_a_deleted_lap():
    a = Analyzer()
    a.seed_bests(31_000, 42_800, 28_400, 102_200)  # from Session History
    a.on_sector(sector(3, 2, 42_600, invalid=True))
    assert a.best_sectors[2] == 42_800


def test_abandoned_lap_is_not_revoked_by_a_later_deleted_lap():
    a = Analyzer()
    a.on_sector(sector(1, 1, 31_000))
    a.on_sector(sector(1, 2, 42_800))
    a.on_sector(sector(1, 3, 28_400))
    a.on_lap(LapComplete(1, 102_200, (31_000, 42_800, 28_400), False))
    a.on_sector(sector(2, 1, 30_800))  # good S1, then a restart: no LapComplete
    a.on_sector(sector(3, 1, 31_500, invalid=True))
    a.on_lap(LapComplete(3, 103_000, (31_500, 43_000, 28_500), True))
    assert a.best_sectors[1] == 30_800
