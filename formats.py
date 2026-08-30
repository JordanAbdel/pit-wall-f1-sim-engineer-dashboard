"""Per-format decoding for F1 UDP telemetry, packet formats 2020-2025.

The app's internal model is the F1 25 shape (see packets.py): lap and
sector times as integer milliseconds, tyre wear as four floats, and so
on. Older games send materially different bytes for the same facts:

  * The header is 24 bytes in 2020-2022 and 29 bytes in 2023-2025
    (2023 added m_gameYear and m_overallFrameIdentifier), which shifts
    the start of every per-car array.
  * 2020 sends lap times as float SECONDS; 2021 onwards send uint32
    milliseconds. Sector minute-parts only exist from 2024.
  * Tyre wear lives in Car Status in 2020 and moved to the Car Damage
    packet (ID 10) in 2021, which 2020 does not send at all.
  * Session History (11) arrived in 2021, Time Trial (14) in 2024.

Each Codec below owns the structs for one format and normalises into
the packets.py NamedTuples, so the rest of the app never branches on
game version. Packet IDs themselves are stable across formats — older
games simply stop at a lower ID — so the packets.LAP_DATA-style
constants remain valid everywhere.

Every struct is size-asserted at import against the packet sizes in the
official specs; a mis-transcribed field fails loudly at startup rather
than silently producing wrong lap times.
"""
import struct

import packets

NUM_CARS = packets.NUM_CARS

# Fill values for fields a given format simply does not transmit, so the
# normalised NamedTuples are always complete.
_LAP_ZEROS = {f: 0 for f in packets.LapData._fields}
_STATUS_ZEROS = {f: 0 for f in packets.CarStatus._fields}


def _lap(**kw) -> packets.LapData:
    d = dict(_LAP_ZEROS, **kw)
    return packets.LapData(**d)


def _status(**kw) -> packets.CarStatus:
    d = dict(_STATUS_ZEROS, **kw)
    return packets.CarStatus(**d)


class Codec:
    """Decodes one packet format into the app's canonical model."""

    # --- subclass contract -------------------------------------------------
    FORMAT = 0
    HEADER = None            # struct for this format's packet header
    SIZES = {}               # packet id -> total bytes, per the official spec
    CAR_LAP = None
    CAR_STATUS = None
    CAR_TELEMETRY_SIZE = 0
    CAR_DAMAGE_SIZE = 0
    LAP_HISTORY = None
    # Capabilities — the app degrades rather than guessing.
    HAS_CAR_DAMAGE = True
    HAS_SESSION_HISTORY = True
    HAS_TIME_TRIAL = False
    WEAR_FROM_CAR_STATUS = False   # 2020 only: wear rides in Car Status

    # Session-type numbering was renumbered in F1 24 when the sprint
    # shootout sessions were inserted at 10-14, pushing Race from 10 to 15
    # and Time Trial from 13 to 18. Getting this wrong is not cosmetic:
    # race mode selects a different reference lap and callout threshold.
    SESSION_TYPE_NAMES = {}
    RACE_SESSION_TYPES = frozenset()
    QUALI_SESSION_TYPES = frozenset()

    def size_ok(self, packet_id: int, n: int) -> bool:
        """Event packets carry a union whose length the published specs get
        wrong more often than not — real F1 22 sends 40 bytes where the spec
        says 36 — and only the 4-char code plus a few detail bytes are ever
        read. So validate a floor for those and an exact size for the rest.
        """
        if packet_id == packets.EVENT:
            return n >= self.header_size + 4
        want = self.SIZES.get(packet_id)
        return want is None or n == want

    # Session lead block and marshal-zone layout are identical in every
    # format from 2020 to 2025; only the header size in front of them moves.
    SESSION_LEAD = struct.Struct("<BbbBHBbBHHBB")
    MARSHAL_ZONE = struct.Struct("<fb")
    MAX_MARSHAL_ZONES = 21
    SH_PREAMBLE = struct.Struct("<BBBBBBB")

    @property
    def header_size(self) -> int:
        return self.HEADER.size

    def expected_size(self, packet_id: int):
        return self.SIZES.get(packet_id)

    def handles(self, packet_id: int) -> bool:
        return packet_id in self.SIZES

    # --- header ------------------------------------------------------------

    def parse_header(self, data: bytes):
        """Normalised to the 2025 PacketHeader shape; fields the format does
        not carry (game_year, overall_frame_identifier) come back as 0."""
        if len(data) < self.HEADER.size:
            return None
        return self._header(data)

    def _header(self, data: bytes):
        raise NotImplementedError

    # --- session (same layout across all formats, header size aside) -------

    def parse_session(self, data: bytes) -> packets.SessionInfo:
        return packets.SessionInfo(
            *self.SESSION_LEAD.unpack_from(data, self.header_size))

    def parse_session_flags(self, data: bytes) -> packets.SessionFlags:
        count_off = self.header_size + self.SESSION_LEAD.size + 3
        num = min(data[count_off], self.MAX_MARSHAL_ZONES)
        base = count_off + 1
        yellows = 0
        for i in range(num):
            _, flag = self.MARSHAL_ZONE.unpack_from(
                data, base + i * self.MARSHAL_ZONE.size)
            if flag == packets.FIA_FLAG_YELLOW:
                yellows += 1
        sc = data[base + self.MAX_MARSHAL_ZONES * self.MARSHAL_ZONE.size]
        return packets.SessionFlags(yellows, sc)

    # --- event -------------------------------------------------------------

    def parse_event(self, data: bytes) -> packets.Event:
        code = data[self.header_size:self.header_size + 4].decode("ascii", "replace")
        return packets.Event(code, data[self.header_size + 4:])

    # --- car telemetry (leading scalar block is identical 2020-2025) -------

    def parse_car_telemetry(self, data: bytes, car_index: int) -> packets.CarTelemetry:
        off = self.header_size + car_index * self.CAR_TELEMETRY_SIZE
        return packets.CarTelemetry(*packets.TELEMETRY_LEAD.unpack_from(data, off))

    # --- motion ------------------------------------------------------------
    # CarMotionData leads with world position X/Y/Z and is 60 bytes per car
    # in every format from 2020 to 2025, so only the header size moves. The
    # circuit lies in the X/Z plane; Y is elevation.

    CAR_MOTION_SIZE = 60
    MOTION_LEAD = struct.Struct("<3f")

    def parse_car_position(self, data: bytes, car_index: int):
        """(x, y, z) world position in metres."""
        off = self.header_size + car_index * self.CAR_MOTION_SIZE
        return self.MOTION_LEAD.unpack_from(data, off)

    # --- tyre wear ---------------------------------------------------------

    def parse_tyre_wear(self, data: bytes, car_index: int):
        """From the Car Damage packet (2021+). 2020 overrides this to read
        Car Status instead; see wear_from_car_status()."""
        off = self.header_size + car_index * self.CAR_DAMAGE_SIZE
        return packets.DAMAGE_LEAD.unpack_from(data, off)

    def wear_from_car_status(self, data: bytes, car_index: int):
        """Per-corner wear out of a Car Status packet, or None when this
        format carries wear in Car Damage instead."""
        return None

    # --- lap data / car status: per-format ---------------------------------

    def parse_lap_data(self, data: bytes, car_index: int) -> packets.LapData:
        raise NotImplementedError

    def parse_car_status(self, data: bytes, car_index: int) -> packets.CarStatus:
        raise NotImplementedError

    # --- session history ---------------------------------------------------

    def parse_session_history(self, data: bytes):
        if not self.HAS_SESSION_HISTORY:
            return None
        pre = self.SH_PREAMBLE.unpack_from(data, self.header_size)
        base = self.header_size + self.SH_PREAMBLE.size
        laps = []
        for i in range(min(pre[1], packets.MAX_HISTORY_LAPS)):
            laps.append(self._lap_history(data, base + i * self.LAP_HISTORY.size))
        return packets.SessionHistory(*pre, laps)

    def _lap_history(self, data: bytes, off: int) -> packets.LapHistory:
        """2021/2022: plain uint16 sector times. 2023+ add minute parts."""
        lap_ms, s1, s2, s3, flags = self.LAP_HISTORY.unpack_from(data, off)
        return packets.LapHistory(lap_ms, s1, s2, s3, flags)

    # --- time trial --------------------------------------------------------

    def parse_time_trial(self, data: bytes):
        if not self.HAS_TIME_TRIAL:
            return None
        sets = [packets.TimeTrialSet(*packets.TT_DATA_SET.unpack_from(
                    data, self.header_size + i * packets.TT_DATA_SET.size))
                for i in range(3)]
        return packets.TimeTrialData(*sets)


# ---------------------------------------------------------------------------
# 2020 — the odd one out: 24-byte header, float-seconds lap times, no Car
# Damage / Session History / Time Trial packets, wear inside Car Status.
# ---------------------------------------------------------------------------

_HEADER_24 = struct.Struct("<HBBBBQfIBB")


def _header_24(codec, data: bytes):
    (fmt, major, minor, pver, pid, uid, stime, frame, pidx,
     sidx) = _HEADER_24.unpack_from(data, 0)
    return packets.PacketHeader(
        packet_format=fmt, game_year=0, game_major_version=major,
        game_minor_version=minor, packet_version=pver, packet_id=pid,
        session_uid=uid, session_time=stime, frame_identifier=frame,
        overall_frame_identifier=frame, player_car_index=pidx,
        secondary_player_car_index=sidx)


class Codec2020(Codec):
    FORMAT = 2020
    HEADER = _HEADER_24
    SIZES = {0: 1464, 1: 251, 2: 1190, 3: 35, 4: 1213, 5: 1102,
             6: 1307, 7: 1344, 8: 839, 9: 1169}
    # float lastLap, float currentLap, u16 s1, u16 s2, float bestLap,
    # u8 bestLapNum, 3x u16 best-lap sectors, 3x (u16 + u8) best-overall
    # sectors, 3x float distances, then 9 u8 status fields.
    CAR_LAP = struct.Struct("<ffHHfBHHHHBHBHBfff9B")
    CAR_STATUS = struct.Struct("<BBBBBfffHHBBH4B3B4B6BbfBfff")
    CAR_TELEMETRY_SIZE = 58
    HAS_CAR_DAMAGE = False
    HAS_SESSION_HISTORY = False
    HAS_TIME_TRIAL = False
    WEAR_FROM_CAR_STATUS = True

    def _header(self, data):
        return _header_24(self, data)

    def parse_lap_data(self, data: bytes, car_index: int) -> packets.LapData:
        off = self.header_size + car_index * self.CAR_LAP.size
        v = self.CAR_LAP.unpack_from(data, off)
        return _lap(
            # seconds -> ms, to match every other format
            last_lap_time_ms=int(round(v[0] * 1000)),
            current_lap_time_ms=int(round(v[1] * 1000)),
            s1_ms_part=v[2], s2_ms_part=v[3],
            # v[4:15] are the best-lap / best-overall sector blocks
            lap_distance=v[15], total_distance=v[16], safety_car_delta=v[17],
            car_position=v[18], current_lap_num=v[19], pit_status=v[20],
            sector=v[21], current_lap_invalid=v[22], penalties=v[23],
            grid_position=v[24], driver_status=v[25], result_status=v[26])

    def parse_car_status(self, data: bytes, car_index: int) -> packets.CarStatus:
        off = self.header_size + car_index * self.CAR_STATUS.size
        v = self.CAR_STATUS.unpack_from(data, off)
        return _status(
            traction_control=v[0], anti_lock_brakes=v[1], fuel_mix=v[2],
            front_brake_bias=v[3], pit_limiter_status=v[4],
            fuel_in_tank=v[5], fuel_capacity=v[6], fuel_remaining_laps=v[7],
            max_rpm=v[8], idle_rpm=v[9], max_gears=v[10], drs_allowed=v[11],
            drs_activation_distance=v[12],
            # v[13:17] tyresWear, v[20:24] tyresDamage, v[24:30] body damage
            actual_tyre_compound=v[17], visual_tyre_compound=v[18],
            tyres_age_laps=v[19], vehicle_fia_flags=v[30],
            ers_store_energy=v[31], ers_deploy_mode=v[32],
            ers_harvested_this_lap_mguk=v[33],
            ers_harvested_this_lap_mguh=v[34], ers_deployed_this_lap=v[35])

    def wear_from_car_status(self, data: bytes, car_index: int):
        off = self.header_size + car_index * self.CAR_STATUS.size
        v = self.CAR_STATUS.unpack_from(data, off)
        return (float(v[13]), float(v[14]), float(v[15]), float(v[16]))


# ---------------------------------------------------------------------------
# 2021 / 2022 — 24-byte header, millisecond lap times, Car Damage and
# Session History present, no Time Trial packet.
# ---------------------------------------------------------------------------

class Codec2021(Codec):
    FORMAT = 2021
    HEADER = _HEADER_24
    SIZES = {0: 1464, 1: 625, 2: 970, 3: 36, 4: 1257, 5: 1102,
             6: 1347, 7: 1058, 8: 839, 9: 1191, 10: 882, 11: 1155}
    CAR_LAP = struct.Struct("<IIHHfff14BHHB")
    CAR_STATUS = struct.Struct("<BBBBBfffHHBBHBBBbfBfffB")
    CAR_TELEMETRY_SIZE = 60
    CAR_DAMAGE_SIZE = 39
    LAP_HISTORY = struct.Struct("<IHHHB")
    HAS_TIME_TRIAL = False

    def _header(self, data):
        return _header_24(self, data)

    def parse_lap_data(self, data: bytes, car_index: int) -> packets.LapData:
        off = self.header_size + car_index * self.CAR_LAP.size
        v = self.CAR_LAP.unpack_from(data, off)
        return _lap(
            last_lap_time_ms=v[0], current_lap_time_ms=v[1],
            s1_ms_part=v[2], s2_ms_part=v[3],
            lap_distance=v[4], total_distance=v[5], safety_car_delta=v[6],
            car_position=v[7], current_lap_num=v[8], pit_status=v[9],
            num_pit_stops=v[10], sector=v[11], current_lap_invalid=v[12],
            penalties=v[13], total_warnings=v[14],
            num_unserved_drive_through_pens=v[15],
            num_unserved_stop_go_pens=v[16], grid_position=v[17],
            driver_status=v[18], result_status=v[19],
            pit_lane_timer_active=v[20], pit_lane_time_in_lane_ms=v[21],
            pit_stop_timer_ms=v[22], pit_stop_should_serve_pen=v[23])

    def parse_car_status(self, data: bytes, car_index: int) -> packets.CarStatus:
        off = self.header_size + car_index * self.CAR_STATUS.size
        v = self.CAR_STATUS.unpack_from(data, off)
        return _status(
            traction_control=v[0], anti_lock_brakes=v[1], fuel_mix=v[2],
            front_brake_bias=v[3], pit_limiter_status=v[4],
            fuel_in_tank=v[5], fuel_capacity=v[6], fuel_remaining_laps=v[7],
            max_rpm=v[8], idle_rpm=v[9], max_gears=v[10], drs_allowed=v[11],
            drs_activation_distance=v[12], actual_tyre_compound=v[13],
            visual_tyre_compound=v[14], tyres_age_laps=v[15],
            vehicle_fia_flags=v[16], ers_store_energy=v[17],
            ers_deploy_mode=v[18], ers_harvested_this_lap_mguk=v[19],
            ers_harvested_this_lap_mguh=v[20], ers_deployed_this_lap=v[21],
            network_paused=v[22])


class Codec2022(Codec2021):
    FORMAT = 2022
    # Lap Data grew two trailing bytes (time-trial PB / rival car indices)
    # and several packets were resized; the per-car structs are unchanged.
    SIZES = {0: 1464, 1: 632, 2: 972, 3: 36, 4: 1257, 5: 1102,
             6: 1347, 7: 1058, 8: 1015, 9: 1191, 10: 948, 11: 1155}
    CAR_DAMAGE_SIZE = 42


# ---------------------------------------------------------------------------
# 2023 — 29-byte header arrives (m_gameYear, m_overallFrameIdentifier).
# Sector minute-parts appear; deltas are still plain uint16.
# ---------------------------------------------------------------------------

class Codec2023(Codec):
    FORMAT = 2023
    HEADER = packets.HEADER
    SIZES = {0: 1349, 1: 644, 2: 1131, 3: 45, 4: 1306, 5: 1107,
             6: 1352, 7: 1239, 8: 1020, 9: 1218, 10: 953, 11: 1460,
             12: 231, 13: 217}
    CAR_LAP = struct.Struct("<IIHBHBHHfff15BHHB")
    CAR_STATUS = packets.CAR_STATUS_STRUCT
    CAR_TELEMETRY_SIZE = 60
    CAR_DAMAGE_SIZE = 42
    LAP_HISTORY = packets.LAP_HISTORY
    HAS_TIME_TRIAL = False

    def _header(self, data):
        return packets.PacketHeader(*packets.HEADER.unpack_from(data, 0))

    def parse_lap_data(self, data: bytes, car_index: int) -> packets.LapData:
        off = self.header_size + car_index * self.CAR_LAP.size
        v = self.CAR_LAP.unpack_from(data, off)
        return _lap(
            last_lap_time_ms=v[0], current_lap_time_ms=v[1],
            s1_ms_part=v[2], s1_min_part=v[3],
            s2_ms_part=v[4], s2_min_part=v[5],
            delta_front_ms_part=v[6], delta_leader_ms_part=v[7],
            lap_distance=v[8], total_distance=v[9], safety_car_delta=v[10],
            car_position=v[11], current_lap_num=v[12], pit_status=v[13],
            num_pit_stops=v[14], sector=v[15], current_lap_invalid=v[16],
            penalties=v[17], total_warnings=v[18],
            corner_cutting_warnings=v[19],
            num_unserved_drive_through_pens=v[20],
            num_unserved_stop_go_pens=v[21], grid_position=v[22],
            driver_status=v[23], result_status=v[24],
            pit_lane_timer_active=v[25], pit_lane_time_in_lane_ms=v[26],
            pit_stop_timer_ms=v[27], pit_stop_should_serve_pen=v[28])

    def parse_car_status(self, data: bytes, car_index: int) -> packets.CarStatus:
        return packets.parse_car_status(data, car_index)

    def _lap_history(self, data: bytes, off: int) -> packets.LapHistory:
        (lap_ms, s1_ms, s1_min, s2_ms, s2_min, s3_ms, s3_min,
         flags) = self.LAP_HISTORY.unpack_from(data, off)
        return packets.LapHistory(lap_ms, s1_ms + 60_000 * s1_min,
                                  s2_ms + 60_000 * s2_min,
                                  s3_ms + 60_000 * s3_min, flags)


# ---------------------------------------------------------------------------
# 2024 / 2025 — Lap Data reaches its current shape (delta minute parts and
# the speed trap fields). 2024 introduces the Time Trial packet.
# ---------------------------------------------------------------------------

class Codec2024(Codec2023):
    FORMAT = 2024
    SIZES = {0: 1349, 1: 753, 2: 1285, 3: 45, 4: 1350, 5: 1133,
             6: 1352, 7: 1239, 8: 1020, 9: 1306, 10: 953, 11: 1460,
             12: 231, 13: 237, 14: 101}
    CAR_LAP = packets.CAR_LAP
    HAS_TIME_TRIAL = True

    def parse_lap_data(self, data: bytes, car_index: int) -> packets.LapData:
        return packets.parse_lap_data(data, car_index)


class Codec2025(Codec2024):
    FORMAT = 2025
    SIZES = dict(packets.EXPECTED_SIZES)
    CAR_DAMAGE_SIZE = packets.CAR_DAMAGE_SIZE


# ---------------------------------------------------------------------------
# Session-type enums. F1 24 inserted the five sprint-shootout sessions at
# 10-14, shifting Race 10->15 and Time Trial 13->18. F1 2020 stopped at 12
# (Time Trial) because Race 3 did not exist yet.
# ---------------------------------------------------------------------------

_PRE_SHOOTOUT = {
    1: "Practice 1", 2: "Practice 2", 3: "Practice 3", 4: "Short Practice",
    5: "Qualifying 1", 6: "Qualifying 2", 7: "Qualifying 3",
    8: "Short Qualifying", 9: "One-Shot Qualifying",
    10: "Race", 11: "Race 2",
}
SESSION_TYPES_2020 = {**_PRE_SHOOTOUT, 12: "Time Trial"}
SESSION_TYPES_2021 = {**_PRE_SHOOTOUT, 12: "Race 3", 13: "Time Trial"}
_CLASSIC_QUALI = frozenset({5, 6, 7, 8, 9})

Codec2020.SESSION_TYPE_NAMES = SESSION_TYPES_2020
Codec2020.RACE_SESSION_TYPES = frozenset({10, 11})
Codec2020.QUALI_SESSION_TYPES = _CLASSIC_QUALI

for _cls in (Codec2021, Codec2022, Codec2023):
    _cls.SESSION_TYPE_NAMES = SESSION_TYPES_2021
    _cls.RACE_SESSION_TYPES = frozenset({10, 11, 12})
    _cls.QUALI_SESSION_TYPES = _CLASSIC_QUALI

# 2024/2025 numbering already lives in packets.py as the app's default.
for _cls in (Codec2024, Codec2025):
    _cls.SESSION_TYPE_NAMES = packets.SESSION_TYPE_NAMES
    _cls.RACE_SESSION_TYPES = frozenset({15, 16, 17})
    _cls.QUALI_SESSION_TYPES = frozenset({5, 6, 7, 8, 9, 10, 11, 12, 13, 14})

del _cls


_CODECS = {c.FORMAT: c() for c in
           (Codec2020, Codec2021, Codec2022, Codec2023, Codec2024, Codec2025)}

SUPPORTED = tuple(sorted(_CODECS))


def codec_for(packet_format: int):
    """Codec for a packet format, or None if unsupported."""
    return _CODECS.get(packet_format)


# ---------------------------------------------------------------------------
# Size assertions — every per-car struct must reproduce the packet total
# published in that year's official spec. A typo in any field type or a
# dropped field changes the stride and trips one of these at import.
# ---------------------------------------------------------------------------

def _check(codec, packet_id, per_car, trailing=0, lead=0):
    total = (codec.header_size + lead + NUM_CARS * per_car + trailing)
    want = codec.SIZES[packet_id]
    assert total == want, (
        f"{codec.FORMAT} packet {packet_id}: structs give {total} bytes, "
        f"spec says {want}")


assert _HEADER_24.size == 24, _HEADER_24.size
assert packets.HEADER.size == 29

for _c in _CODECS.values():
    _check(_c, packets.LAP_DATA, _c.CAR_LAP.size,
           trailing=0 if _c.FORMAT in (2020, 2021) else 2)
    _check(_c, packets.CAR_STATUS, _c.CAR_STATUS.size)
    # 2020-2022 carry per-player extras after the array; 2023+ moved them
    # out into the Motion Ex packet.
    _check(_c, packets.MOTION, _c.CAR_MOTION_SIZE,
           trailing=120 if _c.FORMAT <= 2022 else 0)
    _check(_c, packets.CAR_TELEMETRY, _c.CAR_TELEMETRY_SIZE,
           trailing=7 if _c.FORMAT == 2020 else 3)
    if _c.HAS_CAR_DAMAGE:
        _check(_c, packets.CAR_DAMAGE, _c.CAR_DAMAGE_SIZE)
    if _c.HAS_SESSION_HISTORY:
        _total = (_c.header_size + _c.SH_PREAMBLE.size
                  + packets.MAX_HISTORY_LAPS * _c.LAP_HISTORY.size + 8 * 3)
        assert _total == _c.SIZES[packets.SESSION_HISTORY], (
            f"{_c.FORMAT} session history: {_total} vs "
            f"{_c.SIZES[packets.SESSION_HISTORY]}")
    if _c.HAS_TIME_TRIAL:
        assert (_c.header_size + 3 * packets.TT_DATA_SET.size
                == _c.SIZES[packets.TIME_TRIAL])

del _c


# The packet format is the first uint16 of every F1 UDP header and has been
# since 2018, so it can be read before the rest of the layout is known —
# which is exactly what selects the codec.
FORMAT_FIELD = struct.Struct("<H")

# For log lines: the games brand themselves inconsistently (F1 2020, F1 22).
GAME_NAMES = {2020: "F1 2020", 2021: "F1 2021", 2022: "F1 22",
              2023: "F1 23", 2024: "F1 24", 2025: "F1 25"}
