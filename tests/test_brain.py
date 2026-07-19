"""RaceEngineer phrasing — silence rules, natural deltas, reference lines.
Substring assertions so _pick's variety never breaks the suite."""
import brain
import config
from analysis import LapResult, SectorResult
from brain import RaceEngineer, _delta_phrase


def eng(race=False, quali=False):
    e = RaceEngineer()
    e._rng.seed(0)
    e.race_mode = race
    e.quali_mode = quali
    return e


def sres(lap=2, sector=1, ms=31_000, delta=None, best=False, invalid=False,
         stint=None):
    return SectorResult(lap, sector, ms, delta, best, invalid, None, None, stint)


def lres(lap=2, ms=102_000, delta=None, best=False, invalid=False,
         worst=None, stint=None):
    return LapResult(lap, ms, delta, best, invalid, worst, stint)


def test_delta_phrase():
    cases = {8: "a hundredth", 25: "two hundredths", 95: "a tenth",
             210: "two tenths", 480: "half a second", 900: "nine tenths",
             1000: "a second", 1200: "1.2 seconds", -300: "three tenths"}
    for ms, want in cases.items():
        assert _delta_phrase(ms) == want


def test_on_pace_sector_gets_silence():
    line = eng().sector_line(sres(delta=config.CALLOUT_MIN_DELTA_MS - 1))
    assert line is None


def test_no_baseline_gets_silence():
    assert eng().sector_line(sres(delta=None)) is None


def test_sector_loss_is_conversational():
    line = eng().sector_line(sres(sector=2, delta=300))
    assert "You lost three tenths" in line and "second sector" in line
    assert "Purple" not in line and " k " not in line


def test_big_moment():
    line = eng().sector_line(sres(delta=brain.BIG_MOMENT_MS + 100))
    assert "sector" in line.lower()
    assert "moment" in line or "off" in line.lower()


def test_own_best_sector():
    line = eng().sector_line(sres(delta=-250, best=True))
    assert "your best first sector" in line and "quicker" in line


def test_ref_loss_and_gain():
    loss = eng().sector_line(sres(delta=50), ref_delta_ms=400,
                             ref_label="the rival")
    assert "four tenths to the rival" in loss
    gain = eng().sector_line(sres(delta=50), ref_delta_ms=-300,
                             ref_label="the rival")
    assert "quicker than the rival" in gain


def test_ref_huge_gap_falls_back_to_self():
    # 18 s off the target isn't coaching — self delta (silent here) wins.
    line = eng().sector_line(sres(delta=50), ref_delta_ms=18_000,
                             ref_label="the target")
    assert line is None


def test_best_sector_with_rival_context():
    line = eng().sector_line(sres(delta=-250, best=True), ref_delta_ms=200,
                             ref_label="the rival")
    assert "your best" in line and "behind the rival" in line


def test_first_lap_with_rival():
    line = eng().lap_line(sres(sector=3, delta=None, best=True),
                          lres(lap=1, delta=None),
                          ref_delta_ms=1_400, ref_label="the rival")
    assert "First lap on the board" in line
    assert "1.4 seconds off the rival" in line


def test_new_best_lap_vs_rival():
    line = eng().lap_line(sres(sector=3, delta=-100, best=True),
                          lres(ms=100_900, delta=-1_200, best=True),
                          ref_delta_ms=-100, ref_label="the rival")
    assert "New best lap" in line and "ahead of the rival" in line


def test_invalid_lap():
    line = eng().lap_line(sres(sector=3, delta=100),
                          lres(delta=500, invalid=True))
    assert "deleted" in line and "track limits" in line


def test_race_sector_uses_stint_pace():
    line = eng(race=True)._race_sector_line(
        sres(delta=None, stint=400), "first", None, None)
    assert "four tenths off your stint pace" in line


def test_race_lap_line():
    class Session:
        car_position = 4
        gap_ahead_ms = 1_600
        speed_trap_kph = 0
        tyre_age_laps = None
        fuel_remaining_laps = None
    line = eng(race=True).lap_line(sres(sector=3), lres(ms=93_500),
                                   session=Session())
    assert line.startswith("P4") and "gap to the car ahead" in line


def test_recap_mentions_focus():
    r = {"laps": 6, "best_lap_ms": 100_900, "theoretical_ms": 100_400,
         "weakest_sector": 1, "weakest_gap_ms": 600, "strongest_sector": 3,
         "consistency_ms": 250, "improvement_ms": 400}
    words = eng().recap_words(r, weak_zones=[(3390.0, "braked_early", 15, 3),
                                             (1000.0, "overslowed", 8, 2)],
                              track_id=10)
    assert "6 laps" in words
    assert "Pouhon" in words and "practise one thing" in words
    assert "consistency's good" in words.lower()
