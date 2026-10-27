"""End-to-end: sim.py's scripted 6-lap stint replayed straight into the
Engineer (no UDP, no voice) — the same bytes the Xbox path produces.
Catches regressions anywhere in parser → state → analysis → brain."""
import main as main_mod
import sim


class CaptureAnnouncer:
    def __init__(self):
        self.lines = []

    def say(self, text, priority=False):
        self.lines.append(text)

    def close(self):
        pass


def replay(rival=False, dash=None):
    """Compact port of sim.main()'s send loop, calling handle() directly."""
    announcer = CaptureAnnouncer()
    eng = main_mod.Engineer(announcer, dash=dash, cloud=None)
    session_type = 18 if rival else 1
    tick, frame, sim_t = 0.1, 0, 0.0
    completed = []
    for lap_idx, (s1, s2, s3) in enumerate(sim.LAPS):
        lap_num = lap_idx + 1
        bounds = (s1, s1 + s2, s1 + s2 + s3)
        last_lap_ms = completed[-1][0] if completed else 0
        top_speed, flat_frac = sim.PROFILES[lap_idx]
        t = 0.0
        while t < bounds[2]:
            sector0 = 0 if t < bounds[0] else (1 if t < bounds[1] else 2)
            s1_ms = int(s1 * 1000) if t >= bounds[0] else 0
            s2_ms = int(s2 * 1000) if t >= bounds[1] else 0
            frame += 1
            eng.handle(sim.lap_data_packet(sim_t, frame, lap_num, sector0,
                                           int(t * 1000), s1_ms, s2_ms,
                                           last_lap_ms, t * 51.0))
            throttle, brake, speed = sim.driving_sample(t, top_speed, flat_frac)
            eng.handle(sim.telemetry_packet(sim_t, frame, speed, throttle, brake))
            if frame % 20 == 0:
                eng.handle(sim.session_history_packet(sim_t, frame, completed))
                eng.handle(sim.session_packet(sim_t, frame, session_type))
                if rival:
                    eng.handle(sim.time_trial_packet(sim_t, frame))
            t += tick
            sim_t += tick
        lap_ms = int(round(bounds[2] * 1000))
        completed.append((lap_ms, int(s1 * 1000), int(s2 * 1000), int(s3 * 1000)))
    # line-crossing packets so the final lap completes
    for _ in range(3):
        frame += 1
        eng.handle(sim.lap_data_packet(sim_t, frame, len(sim.LAPS) + 1, 0,
                                       500, 0, 0, completed[-1][0], 25.0))
    return eng, announcer.lines


def test_practice_stint():
    eng, lines = replay()
    assert eng._laps_seen == len(sim.LAPS)
    assert eng.analyzer.best_lap_ms == 100_900          # lap 6
    assert eng.analyzer.best_sectors[1] == 30_600
    joined = " ".join(lines)
    assert "Radio check" in joined
    assert "New best lap" in joined
    assert "laps in a row" in joined                    # streak memory fired
    assert eng.memory.top_issue() is not None
    assert "the rival" not in joined                    # no reference w/o TT
    # The Silverstone benchmark is 84.0s; this stint's theoretical best is
    # 100.9s. An out-of-reach target is not a coaching reference, so the
    # deltas stay against the driver's own bests.
    assert eng.reference.source == "self"
    assert "the target" not in joined


def test_time_trial_with_rival():
    eng, lines = replay(rival=True)
    assert eng.reference.source == "rival"
    assert eng.reference.lap_target() == 101_000
    joined = " ".join(lines)
    assert "the rival" in joined
    # lap 6 (1:40.9) beats the rival's 1:41.0 — the payoff line
    assert "ahead of the rival" in joined
    assert eng._laps_seen == len(sim.LAPS)


def test_dashboard_gets_traces_radio_and_inputs():
    from dashboard import Dashboard
    dash = Dashboard()
    eng, lines = replay(dash=dash)
    eng.handle(sim.car_damage_packet(200.0, 99_999, 12.5))  # replay() sends
    eng.handle(sim.car_status_packet(200.0, 99_999, 5, 20.0))  # neither
    snap = dash.snapshot()
    assert [r["text"] for r in snap["radio"]] == lines[-len(snap["radio"]):]
    assert snap["speedKph"] is not None and snap["gear"] is not None
    assert len(snap["wearCorners"]) == 4
    assert 0 <= snap["ersPct"] <= 100
    assert snap["sessionLabel"].startswith("Silverstone")
    traces = dash.traces()
    assert traces["version"] >= 1
    last, best = traces["last"], traces["best"]
    assert last["lapMs"] == 100_900                     # lap 6, the new best
    # It just became the reference, so it's drawn against the lap it beat.
    assert traces["bestLabel"] == "previous best" and best["lapMs"] == 102_100
    assert last["time"][0] is not None and last["time"][-1] > last["time"][0]
