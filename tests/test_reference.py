"""ReferenceTimes — the rival → target → self priority chain."""
import packets
import reference
import sim


def rival():
    return packets.parse_time_trial(sim.time_trial_packet(0.0, 1)).rival


def test_self_by_default():
    r = reference.ReferenceTimes()
    assert r.source == "self"
    assert r.lap_target() is None and r.sector_target(1) is None
    assert r.label() == "your best"


def test_target_from_benchmarks():
    r = reference.ReferenceTimes()
    r.set_track(10)                       # Spa
    # Until the driver has posted a lap there is nothing to judge the
    # benchmark against, so it stays out of the coaching.
    assert r.source == "self"
    assert r.lap_target() is None and r.sector_target(1) is None
    r.update_own({1: 31_000, 2: 42_500, 3: 28_000})   # 101.5s — on the money
    assert r.source == "target" and r.label() == "the target"
    assert r.lap_target() == reference.BENCHMARKS[10]["target_lap_ms"]


def test_out_of_reach_target_is_not_a_reference():
    """An alien benchmark recited every sector teaches nothing. Melbourne's
    73.5s against a driver lapping in the low 1:20s is 8.5% away — the
    coaching must fall back to the driver's own bests."""
    r = reference.ReferenceTimes()
    r.set_track(0)
    r.update_own({1: 27_708, 2: 18_247, 3: 34_385})   # 80.34s
    assert r.source == "self" and r.label() == "your best"
    assert r.lap_target() is None
    assert r.sector_target(2) is None
    # but it is still reported as the track goal
    assert r.state()["goalMs"] == 73_500


def test_target_returns_once_the_driver_closes_in():
    r = reference.ReferenceTimes()
    r.set_track(0)
    r.update_own({1: 27_708, 2: 18_247, 3: 34_385})
    assert r.source == "self"
    # inside 4% of 73.5s
    r.update_own({1: 25_800, 2: 17_000, 3: 33_000})   # 75.8s
    assert r.source == "target"


def test_rival_ghost_is_used_however_far_away_it_is():
    """The rival is a real lap someone actually drove on this layout, so
    it is a reference even when it is miles quicker."""
    r = reference.ReferenceTimes()
    r.set_track(0)
    r.update_own({1: 40_000, 2: 30_000, 3: 50_000})
    r.update_rival(rival())
    assert r.source == "rival" and r.lap_target() == 101_000


def test_target_sector_split_uses_own_proportions():
    r = reference.ReferenceTimes()
    r.set_track(10)
    r.update_own({1: 31_000, 2: 42_500, 3: 28_000})
    total = 31_000 + 42_500 + 28_000
    want = round(r.lap_target() * 31_000 / total)
    assert r.sector_target(1) == want
    assert sum(r.sector_target(i) for i in (1, 2, 3)) - r.lap_target() <= 2


def test_incomplete_own_sectors_ignored():
    r = reference.ReferenceTimes()
    r.set_track(10)
    r.update_own({1: 31_000, 2: None, 3: 28_000})
    assert r.sector_target(1) is None


def test_rival_wins_over_target():
    r = reference.ReferenceTimes()
    r.set_track(10)
    r.update_rival(rival())
    assert r.source == "rival" and r.label() == "the rival"
    assert r.lap_target() == 101_000
    assert r.sector_target(2) == 42_350


def test_empty_rival_ignored():
    r = reference.ReferenceTimes()
    r.update_rival(rival()._replace(lap_time_ms=0))
    assert r.source == "self"


def test_unknown_track_stays_self():
    r = reference.ReferenceTimes()
    r.set_track(99)
    assert r.source == "self"


def test_state_shape():
    r = reference.ReferenceTimes()
    r.update_rival(rival())
    assert r.state() == {"source": "rival", "lapMs": 101_000,
                         "sectors": [30_600, 42_350, 28_050], "goalMs": None}
    assert reference.ReferenceTimes().state() == \
        {"source": "self", "lapMs": None, "sectors": None, "goalMs": None}
