"""SessionState — the packet-stream → sector/lap event state machine.
A bug here silently corrupts every number the coaching speaks."""
from conftest import UID, make_header, make_lap

from state import LapComplete, SectorComplete, SessionState


def drive(state, laps):
    events = []
    for lap in laps:
        events += state.update_from_lap_data(make_header(), lap)
    return events


def test_full_lap_emits_sectors_and_lap():
    s = SessionState()
    events = drive(s, [
        make_lap(sector=0, current_lap_num=1),                      # resync
        make_lap(sector=1, current_lap_num=1, s1_ms_part=31_200),   # S1 done
        make_lap(sector=2, current_lap_num=1, s1_ms_part=31_200,
                 s2_ms_part=42_800),                                # S2 done
        make_lap(sector=0, current_lap_num=2,                       # line crossed
                 last_lap_time_ms=102_400),
    ])
    kinds = [type(e).__name__ for e in events]
    assert kinds == ["SectorComplete", "SectorComplete", "SectorComplete",
                     "LapComplete"]
    s1, s2, s3, lap = events
    assert (s1.sector, s1.time_ms) == (1, 31_200)
    assert (s2.sector, s2.time_ms) == (2, 42_800)
    assert (s3.sector, s3.time_ms) == (3, 102_400 - 31_200 - 42_800)
    assert lap.lap_time_ms == 102_400
    assert lap.sector_times_ms == (31_200, 42_800, 28_400)
    assert not lap.invalid


def test_invalid_flag_sticks_for_the_lap():
    s = SessionState()
    events = drive(s, [
        make_lap(sector=0),
        make_lap(sector=0, current_lap_invalid=1),   # cut a corner mid-S1
        make_lap(sector=1, s1_ms_part=31_000),       # flag cleared by game
        make_lap(sector=2, s1_ms_part=31_000, s2_ms_part=42_000),
        make_lap(sector=0, current_lap_num=2, last_lap_time_ms=101_000),
    ])
    assert all(e.lap_invalid for e in events if isinstance(e, SectorComplete))
    assert [e.invalid for e in events if isinstance(e, LapComplete)] == [True]


def test_flashback_sector_rewind_resyncs_without_events():
    s = SessionState()
    events = drive(s, [
        make_lap(sector=0),
        make_lap(sector=1, s1_ms_part=31_000),
        make_lap(sector=0, current_lap_num=1),   # flashback: back to S1
        make_lap(sector=1, s1_ms_part=30_500),   # redo S1
    ])
    sectors = [e for e in events if isinstance(e, SectorComplete)]
    assert [e.time_ms for e in sectors] == [31_000, 30_500]
    assert len(events) == 2  # the rewind itself emitted nothing


def test_lap_number_jump_resyncs():
    s = SessionState()
    events = drive(s, [
        make_lap(sector=0, current_lap_num=1),
        make_lap(sector=0, current_lap_num=4),   # dropped packets / restart
    ])
    assert events == [] and s.lap_num == 4


def test_pit_and_garage_pin_state():
    s = SessionState()
    events = drive(s, [
        make_lap(sector=0),
        make_lap(sector=1, s1_ms_part=31_000, pit_status=1),  # entered pits
        make_lap(sector=2, s1_ms_part=31_000, s2_ms_part=42_000,
                 driver_status=0),                            # in garage
    ])
    assert events == []  # pit/garage transitions never fake sector times


def test_new_session_uid_resets():
    s = SessionState()
    drive(s, [make_lap(sector=0), make_lap(sector=1, s1_ms_part=31_000)])
    events = s.update_from_lap_data(
        make_header(session_uid=UID + 1), make_lap(sector=0))
    assert events == []
    assert s.session_uid == UID + 1 and s.s1_ms is None


def test_lap_without_known_sectors_is_dropped():
    s = SessionState()
    # Join mid-lap: line crossing arrives before we ever saw S1/S2 times.
    events = drive(s, [
        make_lap(sector=2, current_lap_num=1),
        make_lap(sector=0, current_lap_num=2, last_lap_time_ms=101_000),
    ])
    assert events == []  # no fabricated sector-3
