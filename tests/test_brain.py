"""RaceEngineer phrasing — silence rules, natural deltas, reference lines.
Substring assertions so _pick's variety never breaks the suite."""
import pytest

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
    words = eng().recap_words(r, weak_zones=[(3740.0, "braked_early", 15, 3),
                                             (1000.0, "overslowed", 8, 2)],
                              track_id=10)
    assert "6 laps" in words
    assert "Pouhon" in words and "practise one thing" in words
    assert "consistency's good" in words.lower()


# ---- a living engineer: streaks, trends, volunteered calls, answers ----

def test_best_streak_is_noticed():
    e = eng()
    s3 = sres(sector=3, delta=-100, best=True)
    lines = [e.lap_line(s3, lres(lap=n, ms=102_000 - n * 300, delta=-300,
                                 best=True)) for n in range(2, 5)]
    assert all("New best lap" in l for l in lines)
    assert lines[0].style == "excited" and lines[0].kind == "best"
    assert "three" in lines[2].lower()


def test_streak_resets_after_a_slower_lap():
    e = eng()
    s3 = sres(sector=3, delta=-100, best=True)
    e.lap_line(s3, lres(lap=2, delta=-300, best=True))
    e.lap_line(sres(sector=3, delta=50), lres(lap=3, delta=600))
    line = e.lap_line(s3, lres(lap=4, delta=-300, best=True))
    assert "in a row" not in line and "bounce" not in line


def test_bounce_back_lap_is_noticed():
    e = eng()
    e.lap_line(sres(sector=3, delta=50), lres(lap=2, ms=103_000, delta=1_500))
    line = e.lap_line(sres(sector=3, delta=50), lres(lap=3, ms=102_200, delta=700))
    assert ("last time round" in line or "quicker than the last" in line
            or "up on the lap before" in line)


def test_last_lap_of_the_race():
    e = eng(race=True)
    e.total_laps = 5

    class Session:
        car_position = 3
        gap_ahead_ms = None
        speed_trap_kph = 0
        tyre_age_laps = None
        fuel_remaining_laps = None
    line = e.lap_line(sres(sector=3), lres(lap=4, ms=93_500), session=Session())
    assert "bring it home" in line


def test_flag_lines_are_urgent():
    line = eng().flag_line("sc")
    assert line.startswith("Safety car, safety car") and line.style == "urgent"


def test_tyre_wear_called_once_per_threshold():
    e = eng()
    assert e.tyre_wear_line([20, 20, 30, 30]) is None
    mid = e.tyre_wear_line([40, 52, 55, 49])
    assert "front left" in mid and "55" in mid
    assert e.tyre_wear_line([40, 52, 58, 49]) is None
    high = e.tyre_wear_line([40, 52, 71, 49])
    assert "71" in high
    assert e.tyre_wear_line([40, 52, 80, 49]) is None
    e.new_tyres_line("SOFT")
    assert e.tyre_wear_line([40, 52, 55, 49]) is not None  # a new set


def test_weather_changes():
    e = eng()
    assert "Rain" in e.weather_line(1, 3)
    assert "stopped" in e.weather_line(4, 2)
    assert "heavier" in e.weather_line(3, 4)
    assert e.weather_line(0, 1) is None


def test_finish_lines():
    assert "P1" in eng().finish_line(1, True)
    assert "podium" in eng().finish_line(3, True)
    assert "P9" in eng().finish_line(9, True)


CTX = {"race": True, "position": 3, "gap_ahead_ms": 1400, "lap": 7,
       "total_laps": 12, "compound": "SOFT", "tyre_age": 6,
       "wear_corners": [20, 22, 30, 28], "fuel_laps": 0.8,
       "best_ms": 88_400, "last_ms": 88_900, "theoretical_ms": 88_000,
       "track_id": 10, "top_issue": (3740.0, "braked_early", 15, 3),
       "weather": 3, "track_temp": 24, "air_temp": 18}


@pytest.mark.parametrize("q, want", [
    ("radio check", "loud and clear"),
    ("what's the gap?", "1.4 seconds"),
    ("what position am I", "P3"),
    ("how are my tyres", "front left"),
    ("how's the fuel", "0.8 laps"),
    ("how many laps left", "5 laps to go"),
    ("what's the weather doing", "light rain"),
    ("where am I losing time?", "Pouhon"),
    ("how's my pace", "one 28 point 4"),
])
def test_answers_come_from_the_data(q, want):
    line = eng().answer(q, CTX)
    assert want.lower() in line.lower()
    assert line.kind == "answer"


def test_unknown_question_asks_again():
    assert "say again" in eng().answer("what's for dinner", CTX).lower()


def test_answers_without_data_do_not_make_things_up():
    e = eng()
    assert "haven't" in e.answer("what's the gap", {"race": True}).lower()
    assert "just you and the clock" in e.answer("gap?", {"race": False})
    assert "no laps" in e.answer("how's my pace", {}).lower()
    assert "not getting" in e.answer("fuel?", {}).lower()
