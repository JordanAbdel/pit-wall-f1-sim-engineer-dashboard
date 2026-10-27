#!/usr/bin/env python3
"""Synthetic F1 25 telemetry sender — end-to-end pipeline test without the Xbox.

Replays a 3-lap scripted stint (lap 2 improves, lap 3 mixed) as
spec-exact UDP packets built from the same structs main.py parses.

  python3 sim.py --speed 20        # 20x time compression (default)
  python3 sim.py --port 20777
  python3 sim.py --game 22         # send F1 22 (packet format 2022) bytes
"""
import argparse
import random
import socket
import struct
import time

import packets

SESSION_UID = random.getrandbits(63) or 1  # fresh session per run, like the real game
# Silverstone, as the Session packet says. Distance covers the whole lap at
# each lap's own pace, so a quicker lap reaches every metre sooner — which is
# what the dashboard's distance-based live delta measures.
TRACK_LENGTH_M = 5891.0
PLAYER_IDX = 3  # non-zero so per-car offset arithmetic is actually exercised

# Scripted sector times per lap, seconds.
LAPS = [
    (31.2, 42.8, 28.4),   # lap 1 — baseline, 1:42.400
    (30.9, 43.1, 28.1),   # lap 2 — new best 1:42.100
    (31.4, 42.6, 28.6),   # lap 3 — slower, braking early (streak 1)
    (31.5, 42.9, 28.3),   # lap 4 — early again ("Same again")
    (31.6, 43.0, 28.4),   # lap 5 — early again (streak call)
    (30.6, 42.4, 27.9),   # lap 6 — new best 1:40.900, later brakes (fix + why-faster)
]

# Per-lap driving profile: (top speed kph, fraction of time at full
# throttle). The flat fraction shifts brake onset, so laps 3-5 brake
# ~70 m earlier than the lap-2 reference and lap 6 brakes later.
PROFILES = [(308, 0.55), (316, 0.62), (296, 0.42),
            (298, 0.44), (297, 0.43), (322, 0.68)]


def driving_sample(t, top_speed, flat_frac):
    """(throttle, brake, speed) at time t — accelerate / brake / part-throttle."""
    f = (t % 7.0) / 7.0
    if f < flat_frac:
        return 1.0, 0.0, int(130 + (top_speed - 130) * (f / flat_frac))
    if f < flat_frac + 0.15:
        return 0.0, 0.9, int(top_speed - (top_speed - 140) * ((f - flat_frac) / 0.15))
    return 0.7, 0.0, 150


def header(packet_id: int, session_time: float, frame: int) -> bytes:
    return packets.HEADER.pack(
        packets.PACKET_FORMAT, 25, 1, 0, 1, packet_id, SESSION_UID,
        session_time, frame, frame, PLAYER_IDX, 255)


def lap_data_packet(session_time, frame, lap_num, sector0, current_ms,
                    s1_ms, s2_ms, last_lap_ms, lap_distance,
                    trap_kph=308.0, position=1, gap_ahead_ms=0) -> bytes:
    player = packets.CAR_LAP.pack(
        last_lap_ms, current_ms,
        s1_ms % 60_000, s1_ms // 60_000,
        s2_ms % 60_000, s2_ms // 60_000,
        gap_ahead_ms % 60_000, gap_ahead_ms // 60_000, 0, 0,
        lap_distance, lap_distance + (lap_num - 1) * 5300.0, 0.0,
        position, lap_num, 0, 0, sector0, 0,
        0, 0, 0, 0, 0, 1,
        1,  # driver_status = flying lap
        2,  # result_status = active
        0, 0, 0, 0,
        trap_kph, 1)
    cars = bytearray(packets.NUM_CARS * packets.CAR_LAP.size)
    cars[PLAYER_IDX * packets.CAR_LAP.size:(PLAYER_IDX + 1) * packets.CAR_LAP.size] = player
    return header(packets.LAP_DATA, session_time, frame) + bytes(cars) + b"\xff\xff"


def session_packet(session_time, frame, session_type=1,
                   yellow_zones=0, sc_status=0) -> bytes:
    lead = packets.SESSION_LEAD.pack(
        1, 32, 24, 0, int(TRACK_LENGTH_M),
        session_type,   # 1 = Practice 1, 15 = Race
        7,   # track = Silverstone
        0, 2400, 3600, 80,
        0)   # game not paused
    zones = b"".join(
        packets.MARSHAL_ZONE.pack(i / 21, 3 if i < yellow_zones else 0)
        for i in range(packets.MAX_MARSHAL_ZONES))
    flags_block = bytes([0, 255, 0, packets.MAX_MARSHAL_ZONES]) + zones \
        + bytes([sc_status, 0])
    body = lead + flags_block
    body += bytes(packets.EXPECTED_SIZES[packets.SESSION]
                  - packets.HEADER.size - len(body))
    return header(packets.SESSION, session_time, frame) + body


def event_packet(session_time, frame, code, details=b"") -> bytes:
    body = code.encode("ascii") + details
    body += bytes(packets.EXPECTED_SIZES[packets.EVENT]
                  - packets.HEADER.size - len(body))
    return header(packets.EVENT, session_time, frame) + body


def car_damage_packet(session_time, frame, wear_pct) -> bytes:
    player = packets.DAMAGE_LEAD.pack(wear_pct, wear_pct + 1.5,
                                      wear_pct - 0.8, wear_pct + 0.6)
    player += bytes(packets.CAR_DAMAGE_SIZE - len(player))
    cars = bytearray(packets.NUM_CARS * packets.CAR_DAMAGE_SIZE)
    cars[PLAYER_IDX * packets.CAR_DAMAGE_SIZE:
         (PLAYER_IDX + 1) * packets.CAR_DAMAGE_SIZE] = player
    return header(packets.CAR_DAMAGE, session_time, frame) + bytes(cars)


def car_status_packet(session_time, frame, tyre_age, fuel_laps) -> bytes:
    player = packets.CAR_STATUS_STRUCT.pack(
        0, 0, 1, 55, 0,
        30.0, 110.0, fuel_laps,
        13000, 3500, 8, 1, 0,
        18, 16, tyre_age, 0,
        0.0, 0.0, 4_000_000.0, 1,
        0.0, 0.0, 0.0, 0)
    cars = bytearray(packets.NUM_CARS * packets.CAR_STATUS_STRUCT.size)
    cars[PLAYER_IDX * 55:(PLAYER_IDX + 1) * 55] = player
    return header(packets.CAR_STATUS, session_time, frame) + bytes(cars)


def telemetry_packet(session_time, frame, speed_kph, throttle=0.95, brake=0.0) -> bytes:
    lead = packets.TELEMETRY_LEAD.pack(speed_kph, throttle, -0.1, brake, 0, 7, 11500, 0)
    car = lead + bytes(packets.CAR_TELEMETRY_SIZE - len(lead))
    cars = bytearray(packets.NUM_CARS * packets.CAR_TELEMETRY_SIZE)
    cars[PLAYER_IDX * 60:(PLAYER_IDX + 1) * 60] = car
    return header(packets.CAR_TELEMETRY, session_time, frame) + bytes(cars) + b"\x00\x00\x00"


def time_trial_packet(session_time, frame) -> bytes:
    """Time Trial data sets — only the rival matters to the app. The rival
    ghost runs a 1:41.0 (30.6 / 42.35 / 28.05), a shade under the script's
    best lap, so rival-reference callouts fire realistically."""
    def data_set(lap_ms, s1, s2, s3):
        return packets.TT_DATA_SET.pack(PLAYER_IDX, 9, lap_ms, s1, s2, s3,
                                        0, 0, 0, 1, 0, 1)
    body = (data_set(0, 0, 0, 0)                        # session best
            + data_set(0, 0, 0, 0)                      # personal best
            + data_set(101000, 30600, 42350, 28050))    # rival
    return header(packets.TIME_TRIAL, session_time, frame) + body


def session_history_packet(session_time, frame, completed) -> bytes:
    """completed: list of (lap_ms, s1_ms, s2_ms, s3_ms) for finished laps."""
    def best_lap_num(key):
        return min(range(len(completed)), key=lambda i: key(completed[i])) + 1 if completed else 0

    pre = packets.SH_PREAMBLE.pack(
        PLAYER_IDX, len(completed), 1,
        best_lap_num(lambda l: l[0]), best_lap_num(lambda l: l[1]),
        best_lap_num(lambda l: l[2]), best_lap_num(lambda l: l[3]))
    entries = b""
    for lap_ms, s1, s2, s3 in completed:
        entries += packets.LAP_HISTORY.pack(
            lap_ms, s1 % 60_000, s1 // 60_000, s2 % 60_000, s2 // 60_000,
            s3 % 60_000, s3 // 60_000, 0x0F)
    entries += bytes((packets.MAX_HISTORY_LAPS - len(completed)) * packets.LAP_HISTORY.size)
    stints = struct.pack("<BBB", 255, 18, 16) + bytes(7 * 3)
    return header(packets.SESSION_HISTORY, session_time, frame) + pre + entries + stints


# --- F1 22 (packet format 2022) -------------------------------------------
# Written out from EA's "Data Output from F1 22" spec rather than borrowed
# from formats.py, so a sim run cross-checks the app's 2022 decoder. Each
# F1 25 packet built above is re-encoded field by field.

HEADER_22 = struct.Struct("<HBBBBQfIBB")             # 24 bytes
LAP_22 = struct.Struct("<IIHHfff14BHHB")             # 43 bytes per car
STATUS_22 = struct.Struct("<BBBBBfffHHBBHBBBbfBfffB")  # 47 bytes per car
DAMAGE_22, TELEMETRY_22 = 42, 60
LAP_HISTORY_22 = struct.Struct("<IHHHB")             # 11 bytes per lap
SIZES_22 = {1: 632, 2: 972, 3: 40, 6: 1347, 7: 1058, 10: 948, 11: 1155}


def to_f1_22(pkt: bytes) -> bytes | None:
    """The same facts as an F1 25 packet, in F1 22's bytes (None for the
    packets F1 22 never sends)."""
    h = packets.parse_header(pkt)
    pid, hs = h.packet_id, packets.HEADER.size
    head = HEADER_22.pack(2022, 1, 19, 1, pid, h.session_uid, h.session_time,
                          h.frame_identifier, h.player_car_index, 255)

    def cars(stride, encode):
        out = bytearray(packets.NUM_CARS * stride)
        out[PLAYER_IDX * stride:(PLAYER_IDX + 1) * stride] = encode()
        return bytes(out)

    if pid == packets.LAP_DATA:
        v = packets.parse_lap_data(pkt, PLAYER_IDX)
        body = cars(LAP_22.size, lambda: LAP_22.pack(
            v.last_lap_time_ms, v.current_lap_time_ms, v.sector1_ms,
            v.sector2_ms, v.lap_distance, v.total_distance,
            v.safety_car_delta, v.car_position, v.current_lap_num,
            v.pit_status, v.num_pit_stops, v.sector, v.current_lap_invalid,
            v.penalties, v.total_warnings, v.num_unserved_drive_through_pens,
            v.num_unserved_stop_go_pens, v.grid_position, v.driver_status,
            v.result_status, v.pit_lane_timer_active,
            v.pit_lane_time_in_lane_ms, v.pit_stop_timer_ms,
            v.pit_stop_should_serve_pen)) + b"\xff\xff"
    elif pid == packets.CAR_STATUS:
        v = packets.parse_car_status(pkt, PLAYER_IDX)
        body = cars(STATUS_22.size, lambda: STATUS_22.pack(
            *v[:17], v.ers_store_energy, v.ers_deploy_mode,
            v.ers_harvested_this_lap_mguk, v.ers_harvested_this_lap_mguh,
            v.ers_deployed_this_lap, v.network_paused))
    elif pid == packets.CAR_DAMAGE:
        wear = packets.parse_tyre_wear(pkt, PLAYER_IDX)
        body = cars(DAMAGE_22, lambda: packets.DAMAGE_LEAD.pack(*wear)
                    + bytes(DAMAGE_22 - 16))
    elif pid == packets.CAR_TELEMETRY:
        off = hs + PLAYER_IDX * packets.CAR_TELEMETRY_SIZE
        body = cars(TELEMETRY_22, lambda: pkt[off:off + TELEMETRY_22]) + bytes(3)
    elif pid == packets.SESSION_HISTORY:
        sh = packets.parse_session_history(pkt)
        laps = b"".join(LAP_HISTORY_22.pack(l.lap_time_ms, l.sector1_ms,
                                            l.sector2_ms, l.sector3_ms,
                                            l.valid_flags) for l in sh.laps)
        laps += bytes((packets.MAX_HISTORY_LAPS - len(sh.laps)) * LAP_HISTORY_22.size)
        body = (packets.SH_PREAMBLE.pack(*sh[:7]) + laps
                + pkt[-24:])                          # tyre stints unchanged
    elif pid in (packets.SESSION, packets.EVENT):
        # Same leading layout in both years; F1 22 sessions are shorter
        # and number Practice 1 as 1 and Race as 10.
        body = bytearray(pkt[hs:hs + SIZES_22[pid] - HEADER_22.size])
        if pid == packets.SESSION and body[6] == 15:
            body[6] = 10
        body = bytes(body)
    else:
        return None                                   # e.g. Time Trial (F1 24+)
    out = head + body
    assert len(out) == SIZES_22[pid], (pid, len(out))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=20777)
    parser.add_argument("--speed", type=float, default=20.0,
                        help="time compression factor (20 = a 1:42 lap in ~5s)")
    parser.add_argument("--race", action="store_true",
                        help="simulate a race session (P4, gaps) instead of practice")
    parser.add_argument("--rival", action="store_true",
                        help="simulate Time Trial with a rival ghost (1:41.0)")
    parser.add_argument("--flags", action="store_true",
                        help="script a yellow, a safety car, a red flag and the chequered flag")
    parser.add_argument("--game", type=int, choices=(22, 25), default=25,
                        help="which game's packet format to send")
    args = parser.parse_args()
    if args.game == 22 and args.rival:
        parser.error("F1 22 has no Time Trial packet; --rival needs --game 25")
    session_type = 15 if args.race else (18 if args.rival else 1)

    raw = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)

    class _Sock:
        def sendto(self, pkt, addr):
            if args.game == 22:
                pkt = to_f1_22(pkt)
            if pkt is not None:
                raw.sendto(pkt, addr)
    sock = _Sock()
    for name, size in [("lap data", len(lap_data_packet(0, 0, 1, 0, 0, 0, 0, 0, 0.0))),
                       ("car status", len(car_status_packet(0, 0, 0, 25.0))),
                       ("telemetry", len(telemetry_packet(0, 0, 280))),
                       ("session history", len(session_history_packet(0, 0, []))),
                       ("session", len(session_packet(0, 0))),
                       ("car damage", len(car_damage_packet(0, 0, 10.0))),
                       ("time trial", len(time_trial_packet(0, 0)))]:
        expected = {"lap data": 1285, "car status": 1239, "telemetry": 1352,
                    "session history": 1460, "session": 753, "car damage": 1041,
                    "time trial": 101}[name]
        assert size == expected, f"{name}: built {size} bytes, spec says {expected}"

    print(f"Sending scripted 3-lap stint to {target[0]}:{target[1]} "
          f"at {args.speed}x speed …")

    tick = 0.1  # sim seconds per packet burst
    frame = 0
    sim_t = 0.0
    completed = []

    for lap_idx, (s1, s2, s3) in enumerate(LAPS):
        lap_num = lap_idx + 1
        bounds = (s1, s1 + s2, s1 + s2 + s3)
        last_lap_ms = completed[-1][0] if completed else 0
        top_speed, flat_frac = PROFILES[lap_idx]
        trap = max(p[0] for p in PROFILES[:lap_idx + 1])
        print(f"— lap {lap_num}: {s1} / {s2} / {s3} = {bounds[2]:.1f}s")
        t = 0.0
        while t < bounds[2]:
            sector0 = 0 if t < bounds[0] else (1 if t < bounds[1] else 2)
            s1_ms = int(s1 * 1000) if t >= bounds[0] else 0
            s2_ms = int(s2 * 1000) if t >= bounds[1] else 0
            frame += 1
            position = 4 if args.race else 1
            gap = max(600, 2600 - 500 * lap_idx) if args.race else 0
            sock.sendto(lap_data_packet(sim_t, frame, lap_num, sector0,
                                        int(t * 1000), s1_ms, s2_ms,
                                        last_lap_ms, t / bounds[2] * TRACK_LENGTH_M,
                                        float(trap),
                                        position, gap), target)
            throttle, brake, speed = driving_sample(t, top_speed, flat_frac)
            sock.sendto(telemetry_packet(sim_t, frame, speed, throttle, brake), target)
            if frame % 10 == 0:
                sock.sendto(car_status_packet(sim_t, frame, lap_idx,
                                              25.0 - lap_idx), target)
                sock.sendto(car_damage_packet(
                    sim_t, frame, 4.0 + 4.2 * (lap_idx + t / bounds[2])), target)
            yellow, sc = 0, 0
            if args.flags:
                if lap_idx == 1 and 10 < t < 25:
                    yellow = 1
                if lap_idx == 2 and 10 < t < 40:
                    sc = 1
                if lap_idx == 2 and 59.9 < t < 60.2:
                    sock.sendto(event_packet(sim_t, frame, "RDFL"), target)
            if frame % 20 == 0:
                sock.sendto(session_history_packet(sim_t, frame, completed), target)
                sock.sendto(session_packet(sim_t, frame, session_type,
                                           yellow, sc), target)
                if args.rival:
                    sock.sendto(time_trial_packet(sim_t, frame), target)
            t += tick
            sim_t += tick
            time.sleep(tick / args.speed)
        lap_ms = int(round(bounds[2] * 1000))
        completed.append((lap_ms, int(s1 * 1000), int(s2 * 1000), int(s3 * 1000)))

    # A few post-race packets so the final lap-complete transition is seen.
    final_lap, final_s1 = len(LAPS) + 1, LAPS[-1][0]
    for _ in range(10):
        frame += 1
        sock.sendto(lap_data_packet(sim_t, frame, final_lap, 0, 500, 0, 0,
                                    completed[-1][0], 25.0,
                                    position=4 if args.race else 1,
                                    gap_ahead_ms=1600 if args.race else 0), target)
        sock.sendto(session_history_packet(sim_t, frame, completed), target)
        sim_t += tick
        time.sleep(tick / args.speed)
    if args.flags:
        sock.sendto(event_packet(sim_t, frame, "CHQF"), target)
    print("Done.")


if __name__ == "__main__":
    main()
