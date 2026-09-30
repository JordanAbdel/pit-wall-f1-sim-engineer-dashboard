"""Engineer-level checks for deleted laps (history backfill, driver DB)
and for spectating, where the header has no player car (index 255)."""
import driver_db
import main as main_mod
import packets
import sim
from dashboard import Dashboard


class CaptureAnnouncer:
    def __init__(self):
        self.lines = []

    def say(self, text, priority=False):
        self.lines.append(text)


def _spectator(pkt: bytes) -> bytes:
    """Rewrite a sim packet's header to player_car_index = 255."""
    h = list(packets.HEADER.unpack_from(pkt, 0))
    h[10] = 255
    return packets.HEADER.pack(*h) + pkt[packets.HEADER.size:]


def test_spectating_skips_per_car_packets_without_errors(caplog):
    dash = Dashboard()
    eng = main_mod.Engineer(CaptureAnnouncer(), dash=dash, cloud=None)
    eng.handle(sim.session_packet(0.0, 1))
    per_car = [
        sim.lap_data_packet(0.1, 2, 1, 0, 1000, 0, 0, 0, 50.0),
        sim.telemetry_packet(0.1, 3, 250),
        sim.car_status_packet(0.1, 4, 3, 20.0),
        sim.car_damage_packet(0.1, 5, 10.0),
    ]
    for pkt in per_car:
        eng.handle(_spectator(pkt))  # raised struct.error before the fix
    assert dash.snapshot()["spectating"] is True
    assert dash.snapshot()["lapNo"] is None
    # player car back: decoding and coaching resume
    eng.handle(per_car[0])
    snap = dash.snapshot()
    assert snap["spectating"] is False and snap["lapNo"] == 1


def test_backfilled_laps_carry_the_deleted_flag():
    dash = Dashboard()
    eng = main_mod.Engineer(CaptureAnnouncer(), dash=dash, cloud=None)
    completed = [(102_200, 31_000, 42_800, 28_400),
                 (101_500, 30_900, 42_600, 28_000)]
    pkt = bytearray(sim.session_history_packet(0.0, 1, completed))
    # lap 2: lap and S3 invalid (valid flags 0x01 lap, 0x08 S3)
    off = packets.HEADER.size + packets.SH_PREAMBLE.size \
        + packets.LAP_HISTORY.size * 2 - 1
    pkt[off] = 0x06
    eng.handle(sim.session_packet(0.0, 1))
    eng.handle(bytes(pkt))
    laps = dash.snapshot()["laps"]
    assert [l["invalid"] for l in laps] == [False, True]
    assert eng.analyzer.best_sectors[3] == 28_400


def test_driver_db_never_quotes_an_all_deleted_session():
    records = [
        {"track_id": 7, "best_lap_ms": 102_200},                     # legacy
        {"track_id": 7, "best_lap_ms": 101_000, "best_lap_valid": True},
        {"track_id": 7, "best_lap_ms": 99_000, "best_lap_valid": False},
    ]
    assert driver_db.last_for_track(records, 7)["best_lap_ms"] == 101_000
    assert driver_db.last_for_track(records[:1], 7)["best_lap_ms"] == 102_200
