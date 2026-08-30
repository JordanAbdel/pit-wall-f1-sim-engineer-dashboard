"""Per-format decoding, 2020-2025.

Each test builds a packet by packing fields in the order the official spec
lists them, then parses it back through the codec. That exercises the
normalisation index mapping — the part most likely to drift by one when a
field is added or removed between game years.
"""
import struct

import formats
import packets
import pytest

PLAYER = 3          # non-zero, so per-car offset arithmetic is real
UID = 0x0123456789ABCDEF


def build_header(codec, packet_id):
    if codec.header_size == 24:
        return struct.pack("<HBBBBQfIBB", codec.FORMAT, 1, 18, 1, packet_id,
                           UID, 12.5, 900, PLAYER, 255)
    return struct.pack("<HBBBBBQfIIBB", codec.FORMAT, codec.FORMAT % 100, 1, 18,
                       1, packet_id, UID, 12.5, 900, 900, PLAYER, 255)


def build_packet(codec, packet_id, per_car: bytes, car_stride: int):
    """Header + a 22-car array with per_car at the player slot, padded to the
    total size this format's spec documents."""
    body = bytearray(car_stride * formats.NUM_CARS)
    body[PLAYER * car_stride:PLAYER * car_stride + len(per_car)] = per_car
    pkt = bytearray(build_header(codec, packet_id) + bytes(body))
    pkt.extend(b"\0" * (codec.expected_size(packet_id) - len(pkt)))
    return bytes(pkt)


# --- lap data, per format ---------------------------------------------------

def lap_bytes_2020():
    """float seconds for lap times; plain uint16 sector times."""
    return struct.pack(
        "<ffHHfBHHHHBHBHBfff9B",
        101.9, 41.0,            # last lap, current lap — SECONDS
        30_600, 42_350,         # sector 1, sector 2 in ms
        101.9, 1,               # best lap time / num
        30_600, 42_350, 28_050,             # best lap sectors
        30_600, 1, 42_350, 1, 28_050, 1,    # best overall sectors + lap nums
        2500.0, 7500.0, 0.0,    # lap distance, total distance, SC delta
        4, 2, 0, 1, 0, 0, 5, 4, 2)          # position, lap, pit, sector, ...


def lap_bytes_2021():
    return struct.pack(
        "<IIHHfff14BHHB",
        101_900, 41_000, 30_600, 42_350,
        2500.0, 7500.0, 0.0,
        4, 2, 0, 0, 1, 0, 0, 0, 0, 0, 5, 4, 2, 0,
        0, 0, 0)


def lap_bytes_2023():
    return struct.pack(
        "<IIHBHBHHfff15BHHB",
        101_900, 41_000,
        30_600, 0,              # s1 ms part + minute part
        42_350, 0,              # s2
        1_200, 5_000,           # delta to car in front / race leader
        2500.0, 7500.0, 0.0,
        4, 2, 0, 0, 1, 0, 0, 0, 0, 0, 0, 5, 4, 2, 0,
        0, 0, 0)


def lap_bytes_2024():
    return struct.pack(
        "<IIHBHBHBHBfff15BHHBfB",
        101_900, 41_000,
        30_600, 0, 42_350, 0,
        1_200, 0, 5_000, 0,
        2500.0, 7500.0, 0.0,
        4, 2, 0, 0, 1, 0, 0, 0, 0, 0, 0, 5, 4, 2, 0,
        0, 0, 0,
        311.0, 2)


LAP_BUILDERS = {
    2020: lap_bytes_2020, 2021: lap_bytes_2021, 2022: lap_bytes_2021,
    2023: lap_bytes_2023, 2024: lap_bytes_2024, 2025: lap_bytes_2024,
}


@pytest.mark.parametrize("fmt", formats.SUPPORTED)
def test_lap_data_normalises_to_milliseconds(fmt):
    """Every format must land on the same canonical numbers, despite 2020
    sending float seconds and the sector layouts differing."""
    codec = formats.codec_for(fmt)
    pkt = build_packet(codec, packets.LAP_DATA, LAP_BUILDERS[fmt](),
                       codec.CAR_LAP.size)
    assert len(pkt) == codec.expected_size(packets.LAP_DATA)

    h = codec.parse_header(pkt)
    assert h.packet_format == fmt
    assert h.packet_id == packets.LAP_DATA
    assert h.player_car_index == PLAYER
    assert h.session_uid == UID

    lap = codec.parse_lap_data(pkt, PLAYER)
    assert lap.last_lap_time_ms == 101_900
    assert lap.current_lap_time_ms == 41_000
    assert lap.sector1_ms == 30_600
    assert lap.sector2_ms == 42_350
    assert lap.lap_distance == 2500.0
    assert lap.total_distance == 7500.0
    assert lap.car_position == 4
    assert lap.current_lap_num == 2
    assert lap.sector == 1
    assert lap.driver_status == 4
    assert lap.result_status == 2
    assert lap.grid_position == 5

    # a different car index must read the zeroed slots, not the player's
    assert codec.parse_lap_data(pkt, 0).current_lap_num == 0


@pytest.mark.parametrize("fmt", formats.SUPPORTED)
def test_every_struct_matches_the_published_packet_size(fmt):
    codec = formats.codec_for(fmt)
    stride = codec.CAR_LAP.size
    trailing = 0 if fmt in (2020, 2021) else 2
    assert (codec.header_size + stride * formats.NUM_CARS + trailing
            == codec.expected_size(packets.LAP_DATA))


# --- car status / tyre wear -------------------------------------------------

def test_2020_reads_tyre_wear_out_of_car_status():
    """2020 sends no Car Damage packet at all — wear is uint8 percentages
    inside Car Status."""
    codec = formats.codec_for(2020)
    per_car = struct.pack(
        "<BBBBBfffHHBBH4B3B4B6BbfBfff",
        1, 1, 1, 55, 0,                 # assists, brake bias, pit limiter
        44.5, 110.0, 12.5,              # fuel in tank / capacity / laps
        13000, 4000, 8, 1, 250,         # rpm, gears, DRS
        31, 33, 29, 27,                 # tyres wear %  (RL RR FL FR)
        16, 16, 4,                      # actual / visual compound, age
        0, 0, 0, 0,                     # tyre damage
        0, 0, 0, 0, 0, 0,               # wing / drs fault / engine / gearbox
        0,                              # FIA flags
        1000.0, 2, 0.0, 0.0, 0.0)       # ERS
    pkt = build_packet(codec, packets.CAR_STATUS, per_car, codec.CAR_STATUS.size)

    st = codec.parse_car_status(pkt, PLAYER)
    assert st.visual_tyre_compound == 16
    assert st.tyres_age_laps == 4
    assert st.fuel_in_tank == pytest.approx(44.5)
    assert st.fuel_remaining_laps == pytest.approx(12.5)
    assert st.max_gears == 8

    wear = codec.wear_from_car_status(pkt, PLAYER)
    assert wear == (31.0, 33.0, 29.0, 27.0)
    assert round(sum(wear) / 4, 1) == 30.0


@pytest.mark.parametrize("fmt", [2021, 2022, 2023, 2024, 2025])
def test_tyre_wear_comes_from_car_damage_from_2021(fmt):
    codec = formats.codec_for(fmt)
    assert codec.HAS_CAR_DAMAGE and not codec.WEAR_FROM_CAR_STATUS
    per_car = struct.pack("<4f", 31.0, 33.0, 29.0, 27.0)
    pkt = build_packet(codec, packets.CAR_DAMAGE, per_car, codec.CAR_DAMAGE_SIZE)
    assert codec.parse_tyre_wear(pkt, PLAYER) == (31.0, 33.0, 29.0, 27.0)


# --- session: identical layout in every format, only the header moves -------

@pytest.mark.parametrize("fmt", formats.SUPPORTED)
def test_session_and_marshal_zones_across_formats(fmt):
    codec = formats.codec_for(fmt)
    lead = struct.pack("<BbbBHBbBHHBB",
                       0, 32, 25, 5, 5891, 18, 7, 0, 600, 900, 60, 0)
    zones = struct.pack("<BBB", 0, 255, 0) + bytes([2])   # spectating..numZones
    zone_bytes = b"".join(
        struct.pack("<fb", i / 21.0, packets.FIA_FLAG_YELLOW if i < 2 else 0)
        for i in range(21))
    body = lead + zones + zone_bytes + bytes([packets.SC_FULL, 0])
    pkt = bytearray(build_header(codec, packets.SESSION) + body)
    pkt.extend(b"\0" * (codec.expected_size(packets.SESSION) - len(pkt)))
    pkt = bytes(pkt)

    info = codec.parse_session(pkt)
    assert info.track_id == 7 and info.session_type == 18
    assert info.track_length == 5891
    assert info.track_temperature == 32 and info.air_temperature == 25

    flags = codec.parse_session_flags(pkt)
    assert flags.yellow_zones == 2
    assert flags.safety_car_status == packets.SC_FULL


# --- capability gating ------------------------------------------------------

def test_capabilities_match_the_year_each_packet_arrived():
    assert not formats.codec_for(2020).HAS_SESSION_HISTORY
    assert not formats.codec_for(2020).HAS_CAR_DAMAGE
    for fmt in (2021, 2022, 2023, 2024, 2025):
        assert formats.codec_for(fmt).HAS_SESSION_HISTORY
    # Time Trial only exists from F1 24
    for fmt in (2020, 2021, 2022, 2023):
        assert not formats.codec_for(fmt).HAS_TIME_TRIAL
        assert formats.codec_for(fmt).parse_time_trial(b"\0" * 200) is None
    for fmt in (2024, 2025):
        assert formats.codec_for(fmt).HAS_TIME_TRIAL


def test_unsupported_format_returns_no_codec():
    assert formats.codec_for(2019) is None
    assert formats.codec_for(0) is None
    assert formats.SUPPORTED == (2020, 2021, 2022, 2023, 2024, 2025)


# --- session history: 11-byte lap records in 2021/2022, 14-byte from 2023 ---

@pytest.mark.parametrize("fmt", [2021, 2022, 2023, 2024, 2025])
def test_session_history_lap_records(fmt):
    codec = formats.codec_for(fmt)
    pre = struct.pack("<BBBBBBB", PLAYER, 2, 1, 1, 1, 1, 1)
    if fmt in (2021, 2022):
        laps = (struct.pack("<IHHHB", 101_900, 30_600, 42_350, 28_050, 0x0F)
                + struct.pack("<IHHHB", 101_100, 30_500, 42_300, 28_300, 0x0F))
    else:
        laps = (struct.pack("<IHBHBHBB", 101_900, 30_600, 0, 42_350, 0, 28_050, 0, 0x0F)
                + struct.pack("<IHBHBHBB", 101_100, 30_500, 0, 42_300, 0, 28_300, 0, 0x0F))
    pkt = bytearray(build_header(codec, packets.SESSION_HISTORY) + pre + laps)
    pkt.extend(b"\0" * (codec.expected_size(packets.SESSION_HISTORY) - len(pkt)))

    hist = codec.parse_session_history(bytes(pkt))
    assert hist.car_idx == PLAYER
    assert hist.num_laps == 2
    assert [l.lap_time_ms for l in hist.laps] == [101_900, 101_100]
    assert hist.laps[0].sector1_ms == 30_600
    assert hist.laps[1].sector3_ms == 28_300


def test_2020_has_no_session_history():
    assert formats.codec_for(2020).parse_session_history(b"\0" * 1200) is None


# --- session-type numbering changed in F1 24 --------------------------------

@pytest.mark.parametrize("fmt,tt,races", [
    (2020, 12, {10, 11}),
    (2021, 13, {10, 11, 12}),
    (2022, 13, {10, 11, 12}),
    (2023, 13, {10, 11, 12}),
    (2024, 18, {15, 16, 17}),
    (2025, 18, {15, 16, 17}),
])
def test_session_type_numbering(fmt, tt, races):
    """F1 24 inserted sprint shootouts at 10-14, moving Race and Time Trial.
    Reading these off the wrong table sends races down the non-race path."""
    codec = formats.codec_for(fmt)
    assert codec.SESSION_TYPE_NAMES[tt] == "Time Trial"
    assert codec.RACE_SESSION_TYPES == races
    for r in races:
        assert "Race" in codec.SESSION_TYPE_NAMES[r]
    # a race must never be mistaken for qualifying
    assert not (races & set(codec.QUALI_SESSION_TYPES))


def test_f1_22_time_trial_is_not_a_sprint_shootout():
    """Regression: session type 13 on F1 22 is Time Trial; the 2025 table
    calls it Short Sprint Shootout."""
    assert formats.codec_for(2022).SESSION_TYPE_NAMES[13] == "Time Trial"
    assert packets.SESSION_TYPE_NAMES[13] == "Short Sprint Shootout"


# --- event packets are floor-checked, not exact-size checked ----------------

def test_event_packet_size_is_a_floor_not_an_exact_match():
    """Real F1 22 sends 40-byte Event packets where the spec says 36. Only
    the 4-char code and a few detail bytes are read, so a longer packet is
    fine — rejecting it drops penalties, safety car and fastest-lap events."""
    codec = formats.codec_for(2022)
    assert codec.size_ok(packets.EVENT, 40)
    assert codec.size_ok(packets.EVENT, 36)
    assert not codec.size_ok(packets.EVENT, codec.header_size + 3)


@pytest.mark.parametrize("fmt", formats.SUPPORTED)
def test_non_event_packets_still_require_an_exact_size(fmt):
    codec = formats.codec_for(fmt)
    exact = codec.expected_size(packets.LAP_DATA)
    assert codec.size_ok(packets.LAP_DATA, exact)
    assert not codec.size_ok(packets.LAP_DATA, exact + 4)
    assert not codec.size_ok(packets.LAP_DATA, exact - 1)


@pytest.mark.parametrize("fmt", formats.SUPPORTED)
def test_event_code_parses_at_the_right_offset(fmt):
    codec = formats.codec_for(fmt)
    pkt = build_header(codec, packets.EVENT) + b"PENA" + bytes(20)
    ev = codec.parse_event(pkt)
    assert ev.code == "PENA"


# --- motion / recorded track maps -------------------------------------------

@pytest.mark.parametrize("fmt", formats.SUPPORTED)
def test_world_position_parses_at_the_right_offset(fmt):
    """CarMotionData leads with world X/Y/Z and is 60 bytes per car in every
    format — only the header size in front of it moves."""
    codec = formats.codec_for(fmt)
    per_car = struct.pack("<3f", 120.5, 8.25, -430.75) + bytes(48)
    pkt = build_packet(codec, packets.MOTION, per_car, codec.CAR_MOTION_SIZE)
    x, y, z = codec.parse_car_position(pkt, PLAYER)
    assert (round(x, 2), round(y, 2), round(z, 2)) == (120.5, 8.25, -430.75)
    # the neighbouring car slot must still be zeroed, i.e. stride is right
    assert codec.parse_car_position(pkt, PLAYER + 1) == (0.0, 0.0, 0.0)
