#!/usr/bin/env python3
"""Synthetic F1 25 telemetry sender — end-to-end pipeline test without the Xbox.

Replays a 3-lap scripted stint (lap 2 improves, lap 3 mixed) as
spec-exact UDP packets built from the same structs main.py parses.

  python3 sim.py --speed 20        # 20x time compression (default)
  python3 sim.py --port 20777
"""
import argparse
import random
import socket
import struct
import time

import packets

SESSION_UID = random.getrandbits(63) or 1  # fresh session per run, like the real game
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
        1, 32, 24, 0, 5891,
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
    args = parser.parse_args()
    session_type = 15 if args.race else (18 if args.rival else 1)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = (args.host, args.port)
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
                                        last_lap_ms, t * 51.0, float(trap),
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
