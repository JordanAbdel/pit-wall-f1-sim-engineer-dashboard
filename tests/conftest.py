"""Shared fixtures — put the repo root on sys.path so tests import the
app modules (packets, state, …) exactly as main.py does."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packets  # noqa: E402
from packets import LapData, PacketHeader  # noqa: E402

UID = 42


def make_header(packet_id=packets.LAP_DATA, session_uid=UID,
                session_time=0.0, frame=1, player_car_index=3):
    return PacketHeader(packets.PACKET_FORMAT, 25, 1, 0, 1, packet_id,
                        session_uid, session_time, frame, frame,
                        player_car_index, 255)


_LAP_DEFAULTS = dict(
    last_lap_time_ms=0, current_lap_time_ms=0,
    s1_ms_part=0, s1_min_part=0, s2_ms_part=0, s2_min_part=0,
    delta_front_ms_part=0, delta_front_min_part=0,
    delta_leader_ms_part=0, delta_leader_min_part=0,
    lap_distance=0.0, total_distance=0.0, safety_car_delta=0.0,
    car_position=1, current_lap_num=1, pit_status=0, num_pit_stops=0,
    sector=0, current_lap_invalid=0, penalties=0, total_warnings=0,
    corner_cutting_warnings=0, num_unserved_drive_through_pens=0,
    num_unserved_stop_go_pens=0, grid_position=1,
    driver_status=1,   # flying lap
    result_status=2, pit_lane_timer_active=0, pit_lane_time_in_lane_ms=0,
    pit_stop_timer_ms=0, pit_stop_should_serve_pen=0,
    speed_trap_fastest_speed=0.0, speed_trap_fastest_lap=1,
)


def make_lap(**overrides) -> LapData:
    fields = dict(_LAP_DEFAULTS)
    fields.update(overrides)
    return LapData(**fields)
