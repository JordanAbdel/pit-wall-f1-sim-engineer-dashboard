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
    assert r.source == "target" and r.label() == "the target"
    assert r.lap_target() == reference.BENCHMARKS[10]["target_lap_ms"]
    assert r.sector_target(1) is None     # no own sectors yet — no split


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
                         "sectors": [30_600, 42_350, 28_050]}
    assert reference.ReferenceTimes().state() == \
        {"source": "self", "lapMs": None, "sectors": None}
