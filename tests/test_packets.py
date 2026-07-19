"""Parser round-trips: build spec-exact bytes with sim.py's builders,
parse with packets.py, check the fields that the app actually consumes."""
import packets
import sim


def test_lap_data_round_trip():
    pkt = sim.lap_data_packet(1.5, 7, lap_num=2, sector0=1, current_ms=41_000,
                              s1_ms=95_500, s2_ms=0, last_lap_ms=101_900,
                              lap_distance=2500.0, trap_kph=311.0,
                              position=4, gap_ahead_ms=61_200)
    assert len(pkt) == packets.EXPECTED_SIZES[packets.LAP_DATA]
    h = packets.parse_header(pkt)
    assert h.packet_id == packets.LAP_DATA and h.player_car_index == sim.PLAYER_IDX
    lap = packets.parse_lap_data(pkt, h.player_car_index)
    assert lap.current_lap_num == 2 and lap.sector == 1
    assert lap.current_lap_time_ms == 41_000
    assert lap.sector1_ms == 95_500          # minute-part reassembly (1:35.5)
    assert lap.last_lap_time_ms == 101_900
    assert lap.lap_distance == 2500.0
    assert lap.speed_trap_fastest_speed == 311.0
    assert lap.car_position == 4
    # gap ahead crosses the minute boundary too
    assert lap.delta_front_ms_part + 60_000 * lap.delta_front_min_part == 61_200
    # a different car index reads zeroed data, not the player's
    other = packets.parse_lap_data(pkt, 0)
    assert other.current_lap_num == 0


def test_session_and_flags():
    pkt = sim.session_packet(0.0, 1, session_type=18, yellow_zones=2, sc_status=1)
    info = packets.parse_session(pkt)
    assert info.track_id == 7 and info.session_type == 18
    assert info.track_length == 5891 and not info.game_paused
    flags = packets.parse_session_flags(pkt)
    assert flags.yellow_zones == 2
    assert flags.safety_car_status == packets.SC_FULL


def test_time_trial_rival_set():
    pkt = sim.time_trial_packet(0.0, 1)
    assert len(pkt) == packets.EXPECTED_SIZES[packets.TIME_TRIAL]
    tt = packets.parse_time_trial(pkt)
    assert tt.rival.lap_time_ms == 101_000
    assert (tt.rival.sector1_ms, tt.rival.sector2_ms, tt.rival.sector3_ms) \
        == (30_600, 42_350, 28_050)
    assert tt.session_best.lap_time_ms == 0  # unused sets stay empty


def test_car_status_and_damage():
    pkt = sim.car_status_packet(0.0, 1, tyre_age=5, fuel_laps=12.5)
    status = packets.parse_car_status(pkt, sim.PLAYER_IDX)
    assert status.tyres_age_laps == 5 and status.fuel_remaining_laps == 12.5
    assert status.visual_tyre_compound == 16  # soft
    dmg = sim.car_damage_packet(0.0, 1, wear_pct=20.0)
    wear = packets.parse_tyre_wear(dmg, sim.PLAYER_IDX)
    assert min(wear) > 18 and max(wear) < 22


def test_telemetry():
    pkt = sim.telemetry_packet(0.0, 1, speed_kph=287, throttle=0.8, brake=0.1)
    tel = packets.parse_car_telemetry(pkt, sim.PLAYER_IDX)
    assert tel.speed_kph == 287
    assert abs(tel.throttle - 0.8) < 1e-6 and abs(tel.brake - 0.1) < 1e-6


def test_session_history():
    completed = [(101_900, 31_200, 42_800, 27_900),
                 (161_500, 95_500, 38_000, 28_000)]  # >1 min sector 1
    pkt = sim.session_history_packet(0.0, 1, completed)
    hist = packets.parse_session_history(pkt)
    assert hist.car_idx == sim.PLAYER_IDX and hist.num_laps == 2
    assert hist.laps[0].lap_time_ms == 101_900
    assert hist.laps[1].sector1_ms == 95_500   # minute-part reassembly
    assert hist.best_lap_time_lap_num == 1


def test_events():
    red = sim.event_packet(0.0, 1, "RDFL")
    assert packets.parse_event(red).code == "RDFL"
    pen = sim.event_packet(0.0, 1, "PENA",
                           bytes([4, 7, sim.PLAYER_IDX, 255, 5, 2, 0]))
    ev = packets.parse_event(pen)
    p = packets.parse_penalty_event(ev)
    assert p.penalty_type == 4 and p.vehicle_idx == sim.PLAYER_IDX and p.time == 5
    scar = sim.event_packet(0.0, 1, "SCAR", bytes([1, 1]))
    sc = packets.parse_safety_car_event(packets.parse_event(scar))
    assert sc.car_type == 1 and sc.event_type == 1
