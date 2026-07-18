"""F1 25 UDP packet parsing (packet format 2025).

Struct layouts follow EA's official "Data Output from F1 25 v3" spec.
All values are little-endian, packed, no padding. F1 25 differs from
F1 23/24 — e.g. Lap Data gained per-car speed-trap fields — so sizes
below are validated at runtime against every received packet.
"""
import struct
from typing import NamedTuple

PACKET_FORMAT = 2025

# Packet IDs
MOTION = 0
SESSION = 1
LAP_DATA = 2
EVENT = 3
PARTICIPANTS = 4
CAR_SETUPS = 5
CAR_TELEMETRY = 6
CAR_STATUS = 7
FINAL_CLASSIFICATION = 8
LOBBY_INFO = 9
CAR_DAMAGE = 10
SESSION_HISTORY = 11
TYRE_SETS = 12
MOTION_EX = 13
TIME_TRIAL = 14
LAP_POSITIONS = 15

PACKET_NAMES = {
    0: "Motion", 1: "Session", 2: "Lap Data", 3: "Event", 4: "Participants",
    5: "Car Setups", 6: "Car Telemetry", 7: "Car Status", 8: "Final Classification",
    9: "Lobby Info", 10: "Car Damage", 11: "Session History", 12: "Tyre Sets",
    13: "Motion Ex", 14: "Time Trial", 15: "Lap Positions",
}

# Total packet sizes per the F1 25 spec, used to validate real traffic.
EXPECTED_SIZES = {
    0: 1349, 1: 753, 2: 1285, 3: 45, 4: 1284, 5: 1133, 6: 1352, 7: 1239,
    8: 1042, 9: 954, 10: 1041, 11: 1460, 12: 231, 13: 273, 14: 101, 15: 1131,
}

NUM_CARS = 22


# --- Header (29 bytes) ---

HEADER = struct.Struct("<HBBBBBQfIIBB")


class PacketHeader(NamedTuple):
    packet_format: int
    game_year: int
    game_major_version: int
    game_minor_version: int
    packet_version: int
    packet_id: int
    session_uid: int
    session_time: float
    frame_identifier: int
    overall_frame_identifier: int
    player_car_index: int
    secondary_player_car_index: int


def parse_header(data: bytes) -> PacketHeader | None:
    if len(data) < HEADER.size:
        return None
    return PacketHeader(*HEADER.unpack_from(data, 0))


# --- Lap Data (packet ID 2) — 57 bytes per car in F1 25 ---
# F1 25 added m_speedTrapFastestSpeed / m_speedTrapFastestLap at the tail.

CAR_LAP = struct.Struct("<IIHBHBHBHBfff15BHHBfB")


class LapData(NamedTuple):
    last_lap_time_ms: int
    current_lap_time_ms: int
    s1_ms_part: int
    s1_min_part: int
    s2_ms_part: int
    s2_min_part: int
    delta_front_ms_part: int
    delta_front_min_part: int
    delta_leader_ms_part: int
    delta_leader_min_part: int
    lap_distance: float
    total_distance: float
    safety_car_delta: float
    car_position: int
    current_lap_num: int
    pit_status: int             # 0 = none, 1 = pitting, 2 = in pit area
    num_pit_stops: int
    sector: int                 # 0 = sector 1, 1 = sector 2, 2 = sector 3
    current_lap_invalid: int    # 0 = valid, 1 = invalid
    penalties: int
    total_warnings: int
    corner_cutting_warnings: int
    num_unserved_drive_through_pens: int
    num_unserved_stop_go_pens: int
    grid_position: int
    driver_status: int          # 0 = in garage, 1 = flying lap, 2 = in lap, 3 = out lap, 4 = on track
    result_status: int
    pit_lane_timer_active: int
    pit_lane_time_in_lane_ms: int
    pit_stop_timer_ms: int
    pit_stop_should_serve_pen: int
    speed_trap_fastest_speed: float
    speed_trap_fastest_lap: int

    @property
    def sector1_ms(self) -> int:
        return self.s1_ms_part + 60_000 * self.s1_min_part

    @property
    def sector2_ms(self) -> int:
        return self.s2_ms_part + 60_000 * self.s2_min_part


def parse_lap_data(data: bytes, car_index: int) -> LapData:
    offset = HEADER.size + car_index * CAR_LAP.size
    return LapData(*CAR_LAP.unpack_from(data, offset))


# --- Session (packet ID 1) — leading fields only ---

SESSION_LEAD = struct.Struct("<BbbBHBbBHHBB")


class SessionInfo(NamedTuple):
    weather: int
    track_temperature: int
    air_temperature: int
    total_laps: int
    track_length: int
    session_type: int
    track_id: int
    formula: int
    session_time_left: int
    session_duration: int
    pit_speed_limit: int
    game_paused: int


TRACK_NAMES = {
    0: "Melbourne", 2: "Shanghai", 3: "Sakhir", 4: "Catalunya", 5: "Monaco",
    6: "Montreal", 7: "Silverstone", 9: "Hungaroring", 10: "Spa", 11: "Monza",
    12: "Singapore", 13: "Suzuka", 14: "Abu Dhabi", 15: "Texas", 16: "Brazil",
    17: "Austria", 19: "Mexico", 20: "Baku", 26: "Zandvoort", 27: "Imola",
    29: "Jeddah", 30: "Miami", 31: "Las Vegas", 32: "Losail",
}

SESSION_TYPE_NAMES = {
    1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short Practice",
    5: "Qualifying 1", 6: "Qualifying 2", 7: "Qualifying 3", 8: "Short Qualifying",
    9: "One-Shot Qualifying", 10: "Sprint Shootout 1", 11: "Sprint Shootout 2",
    12: "Sprint Shootout 3", 13: "Short Sprint Shootout",
    14: "One-Shot Sprint Shootout", 15: "Race", 16: "Race 2", 17: "Race 3",
    18: "Time Trial",
}


def parse_session(data: bytes) -> SessionInfo:
    return SessionInfo(*SESSION_LEAD.unpack_from(data, HEADER.size))


# --- Session flags: marshal zones + safety car, after the lead block ---
# Layout after SESSION_LEAD: isSpectating(1), spectatorCarIndex(1),
# sliProNativeSupport(1), numMarshalZones(1), MarshalZone[21] (float
# zoneStart + int8 zoneFlag), safetyCarStatus(1), networkGame(1).

MARSHAL_ZONE = struct.Struct("<fb")
_ZONES_COUNT_OFFSET = HEADER.size + SESSION_LEAD.size + 3
MAX_MARSHAL_ZONES = 21

# safetyCarStatus: 0 none, 1 full, 2 virtual, 3 formation lap
SC_NONE, SC_FULL, SC_VIRTUAL, SC_FORMATION = 0, 1, 2, 3
FIA_FLAG_BLUE, FIA_FLAG_YELLOW = 2, 3


class SessionFlags(NamedTuple):
    yellow_zones: int
    safety_car_status: int


def parse_session_flags(data: bytes) -> SessionFlags:
    num = min(data[_ZONES_COUNT_OFFSET], MAX_MARSHAL_ZONES)
    zones_base = _ZONES_COUNT_OFFSET + 1
    yellows = 0
    for i in range(num):
        _, flag = MARSHAL_ZONE.unpack_from(data, zones_base + i * MARSHAL_ZONE.size)
        if flag == FIA_FLAG_YELLOW:
            yellows += 1
    sc_status = data[zones_base + MAX_MARSHAL_ZONES * MARSHAL_ZONE.size]
    return SessionFlags(yellows, sc_status)


# --- Event (packet ID 3) — 4-char code + union details ---

EVENT_DETAILS_OFFSET = HEADER.size + 4


class Event(NamedTuple):
    code: str
    details: bytes


def parse_event(data: bytes) -> Event:
    code = data[HEADER.size:HEADER.size + 4].decode("ascii", "replace")
    return Event(code, data[EVENT_DETAILS_OFFSET:])


class SafetyCarEvent(NamedTuple):
    car_type: int    # 0 none, 1 full, 2 virtual, 3 formation
    event_type: int  # 0 deployed, 1 returning, 2 returned, 3 resume race


def parse_safety_car_event(ev: Event) -> SafetyCarEvent:
    return SafetyCarEvent(ev.details[0], ev.details[1])


class PenaltyEvent(NamedTuple):
    penalty_type: int       # 0 drive-through, 1 stop-go, 4 time penalty, ...
    infringement_type: int
    vehicle_idx: int
    other_vehicle_idx: int
    time: int               # seconds, 255 if n/a
    lap_num: int
    places_gained: int


def parse_penalty_event(ev: Event) -> PenaltyEvent:
    return PenaltyEvent(*ev.details[:7])


# --- Car Damage (packet ID 10) — 46 bytes per car; wear floats lead ---

CAR_DAMAGE_SIZE = 46
DAMAGE_LEAD = struct.Struct("<4f")


def parse_tyre_wear(data: bytes, car_index: int) -> tuple:
    """Per-corner tyre wear percentages (RL, RR, FL, FR)."""
    offset = HEADER.size + car_index * CAR_DAMAGE_SIZE
    return DAMAGE_LEAD.unpack_from(data, offset)


# --- Car Telemetry (packet ID 6) — 60 bytes per car ---
# Full per-car struct is 60 bytes; v1 only reads the leading scalar block.

CAR_TELEMETRY_SIZE = 60
TELEMETRY_LEAD = struct.Struct("<HfffBbHB")


class CarTelemetry(NamedTuple):
    speed_kph: int
    throttle: float
    steer: float
    brake: float
    clutch: int
    gear: int
    engine_rpm: int
    drs: int


def parse_car_telemetry(data: bytes, car_index: int) -> CarTelemetry:
    offset = HEADER.size + car_index * CAR_TELEMETRY_SIZE
    return CarTelemetry(*TELEMETRY_LEAD.unpack_from(data, offset))


# --- Car Status (packet ID 7) — 55 bytes per car ---

CAR_STATUS_STRUCT = struct.Struct("<BBBBBfffHHBBHBBBbfffBfffB")


class CarStatus(NamedTuple):
    traction_control: int
    anti_lock_brakes: int
    fuel_mix: int
    front_brake_bias: int
    pit_limiter_status: int
    fuel_in_tank: float
    fuel_capacity: float
    fuel_remaining_laps: float
    max_rpm: int
    idle_rpm: int
    max_gears: int
    drs_allowed: int
    drs_activation_distance: int
    actual_tyre_compound: int
    visual_tyre_compound: int
    tyres_age_laps: int
    vehicle_fia_flags: int
    engine_power_ice: float
    engine_power_mguk: float
    ers_store_energy: float
    ers_deploy_mode: int
    ers_harvested_this_lap_mguk: float
    ers_harvested_this_lap_mguh: float
    ers_deployed_this_lap: float
    network_paused: int


def parse_car_status(data: bytes, car_index: int) -> CarStatus:
    offset = HEADER.size + car_index * CAR_STATUS_STRUCT.size
    return CarStatus(*CAR_STATUS_STRUCT.unpack_from(data, offset))


# --- Time Trial (packet ID 14) — three data sets: session best, personal
# best, and the RIVAL ghost. The rival is the only online reference the
# game ever broadcasts: its lap + sector times, live, in Time Trial.

TT_DATA_SET = struct.Struct("<BBIIIIBBBBBB")


class TimeTrialSet(NamedTuple):
    car_idx: int
    team_id: int
    lap_time_ms: int
    sector1_ms: int
    sector2_ms: int
    sector3_ms: int
    traction_control: int
    gearbox_assist: int
    anti_lock_brakes: int
    equal_car_performance: int
    custom_setup: int
    valid: int


class TimeTrialData(NamedTuple):
    session_best: TimeTrialSet
    personal_best: TimeTrialSet
    rival: TimeTrialSet


def parse_time_trial(data: bytes) -> TimeTrialData:
    sets = [TimeTrialSet(*TT_DATA_SET.unpack_from(
                data, HEADER.size + i * TT_DATA_SET.size))
            for i in range(3)]
    return TimeTrialData(*sets)


# --- Session History (packet ID 11) — one car per packet ---

SH_PREAMBLE = struct.Struct("<BBBBBBB")
LAP_HISTORY = struct.Struct("<IHBHBHBB")
MAX_HISTORY_LAPS = 100


class LapHistory(NamedTuple):
    lap_time_ms: int
    sector1_ms: int
    sector2_ms: int
    sector3_ms: int
    valid_flags: int  # 0x01 lap, 0x02 s1, 0x04 s2, 0x08 s3


class SessionHistory(NamedTuple):
    car_idx: int
    num_laps: int
    num_tyre_stints: int
    best_lap_time_lap_num: int
    best_sector1_lap_num: int
    best_sector2_lap_num: int
    best_sector3_lap_num: int
    laps: list  # of LapHistory, length num_laps


def parse_session_history(data: bytes) -> SessionHistory:
    pre = SH_PREAMBLE.unpack_from(data, HEADER.size)
    laps = []
    base = HEADER.size + SH_PREAMBLE.size
    for i in range(min(pre[1], MAX_HISTORY_LAPS)):
        (lap_ms, s1_ms, s1_min, s2_ms, s2_min, s3_ms, s3_min,
         flags) = LAP_HISTORY.unpack_from(data, base + i * LAP_HISTORY.size)
        laps.append(LapHistory(
            lap_ms,
            s1_ms + 60_000 * s1_min,
            s2_ms + 60_000 * s2_min,
            s3_ms + 60_000 * s3_min,
            flags,
        ))
    return SessionHistory(*pre, laps)


assert HEADER.size == 29
assert CAR_LAP.size == 57
assert TT_DATA_SET.size == 24
assert HEADER.size + 3 * TT_DATA_SET.size == EXPECTED_SIZES[TIME_TRIAL]
assert CAR_STATUS_STRUCT.size == 55
assert LAP_HISTORY.size == 14
assert HEADER.size + NUM_CARS * CAR_LAP.size + 2 == EXPECTED_SIZES[LAP_DATA]
assert HEADER.size + NUM_CARS * CAR_TELEMETRY_SIZE + 3 == EXPECTED_SIZES[CAR_TELEMETRY]
assert HEADER.size + NUM_CARS * CAR_STATUS_STRUCT.size == EXPECTED_SIZES[CAR_STATUS]
assert HEADER.size + NUM_CARS * CAR_DAMAGE_SIZE == EXPECTED_SIZES[CAR_DAMAGE]
assert (HEADER.size + SH_PREAMBLE.size + MAX_HISTORY_LAPS * LAP_HISTORY.size
        + 8 * 3 == EXPECTED_SIZES[SESSION_HISTORY])
