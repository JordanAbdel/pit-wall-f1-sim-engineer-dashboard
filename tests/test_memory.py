"""CornerMemory — repeat/streak detection, fixes, profile chips."""
from memory import CornerMemory
from trace import CornerFinding


def loss(onset=1000.0, cause="braked_early", metres=-20.0, kph=0):
    return CornerFinding(onset, cause, metres, kph)


def gain(onset=1000.0, cause="later_brakes"):
    return CornerFinding(onset, cause, 20.0, 5, gain=True)


def test_first_repeat_streak():
    m = CornerMemory()
    assert m.note_sector(1, [loss()]).kind == "first"
    assert m.note_sector(2, [loss()]).kind == "repeat"
    v = m.note_sector(3, [loss()])
    assert v.kind == "streak" and v.streak == 3


def test_gap_lap_breaks_the_streak():
    m = CornerMemory()
    m.note_sector(1, [loss()])
    m.note_sector(3, [loss()])          # lap 2 was clean there
    assert m.note_sector(4, [loss()]).kind == "repeat"


def test_fix_acknowledged_after_streak():
    m = CornerMemory()
    for lap in (1, 2):
        m.note_sector(lap, [loss()])
    v = m.note_sector(3, [gain()])      # same zone comes good
    assert v is not None and v.kind == "fixed"
    assert v.fixed_onset == 1000.0


def test_worst_zones_ranking_and_top_issue():
    m = CornerMemory()
    for lap in (1, 2, 3):
        m.note_sector(lap, [loss(onset=1000.0)])
    for lap in (2, 3):
        m.note_sector(lap, [loss(onset=2000.0, cause="overslowed", kph=-8,
                                 metres=0)])
    zones = m.worst_zones(5)
    assert zones[0][0] == 1000.0 and zones[0][3] == 3
    assert zones[1][0] == 2000.0
    top = m.top_issue()                 # needs >= 3 flagged laps
    assert top[0] == 1000.0 and top[1] == "braked_early"


def test_zone_not_double_counted_within_a_lap():
    m = CornerMemory()
    m.note_sector(1, [loss()])
    m.note_sector(1, [loss()])          # sector-boundary double match
    assert m.worst_zones(1, min_laps=1)[0][3] == 1


def test_profile_chips():
    m = CornerMemory()
    for lap in range(1, 5):
        m.note_sector(lap, [loss()])
    chips = m.profile_chips()
    assert "EARLY ON BRAKES" in chips
    assert m.profile_chips(consistency_ms=250, lap_count=6)[-1] == "CONSISTENT"
    assert CornerMemory().profile_chips() == []
