#!/usr/bin/env python3
"""F1 25 voice race engineer — UDP in, spoken sector/lap coaching out.

Usage:
  python3 main.py            # full pipeline: parse -> analyze -> speak
  python3 main.py --raw      # Phase 1: log raw packet sizes + headers only
  python3 main.py --no-tts   # everything except voice (console only)

Xbox setup: Settings -> Telemetry Settings -> UDP Telemetry ON,
IP = this Mac's LAN IP, port 20777. Any UDP format from 2020 to 2025
works — the format is detected from the first packet and decoded by the
matching codec in formats.py, so no setting has to match the game.
"""
import argparse
import logging
import logging.handlers
import socket
import sys
import threading
import time
import traceback
import webbrowser
from collections import deque

import analysis
import cloud as cloud_mod
import config
import driver_db
import formats
import packets
import reference
import trace
import track_map
from analysis import Analyzer
from brain import RaceEngineer
from dashboard import Dashboard
from memory import CornerMemory
from speak import make_announcer
from state import LapComplete, SectorComplete, SessionState

CORNER_NOTE_MIN_DELTA_MS = 200  # only localize losses worth talking about
LIVE_CORNER_COOLDOWN_S = 10     # min gap between mid-lap corner calls

STORY_CAUSE_WORDS = {
    "braked_early": "braking", "overslowed": "apex", "slow_exit": "exit",
    "carried_speed": "entry speed", "later_brakes": "braking",
    "earlier_throttle": "exit",
}

VISUAL_COMPOUND_NAMES = {16: "SOFT", 17: "MEDIUM", 18: "HARD", 7: "INTER", 8: "WET"}

log = logging.getLogger("engineer")

# In menus F1 25 churns through session UIDs packet-burst to packet-burst;
# in a real session the UID is rock solid (verified against the Xbox).
# Only treat a UID change as a new session once it has stayed put this long.
SESSION_STABLE_PACKETS = 20


def fmt_ms(ms: int) -> str:
    minutes, rem = divmod(ms, 60_000)
    return f"{minutes}:{rem / 1000:06.3f}" if minutes else f"{rem / 1000:.3f}"


def fmt_delta(delta_ms: int | None) -> str:
    if delta_ms is None:
        return "no baseline"
    return f"{delta_ms / 1000:+.3f}s"


# Packets indexed by the player's car. When there is no player car
# (spectating, some online lobby states) the header's index is 255, which
# points past the 22-car array, so these must be skipped, not decoded.
PER_CAR_PACKETS = frozenset({packets.LAP_DATA, packets.CAR_STATUS,
                             packets.CAR_DAMAGE, packets.MOTION,
                             packets.CAR_TELEMETRY})


class Engineer:
    def __init__(self, announcer, dash: Dashboard | None = None, cloud=None):
        self.cloud = cloud
        self.state = SessionState()
        self.analyzer = Analyzer()
        self.brain = RaceEngineer()
        self.announcer = announcer
        self.dash = dash
        self._warned_sizes = set()
        self._warned_format = False
        self._spectating = False
        self._raw_logged = 0
        # Resolved from the first packet that arrives — the game decides
        # which UDP format we speak, not us.
        self.codec = None
        self._last_history_check = 0.0
        self._pending_s3 = None
        self._laps_seen = 0
        self._session_uid = None
        self._candidate_uid = None
        self._candidate_count = 0
        self._track_id = None
        self._track_length = None
        self._race_mode = False
        self._session_type = None
        self.memory = CornerMemory()
        self._stories = {}
        self._db = driver_db.load()
        self._history_announced = False
        self._wear_pct = None
        self._track_maps = track_map.load()
        self._map_rec = track_map.MapRecorder()
        self.reference = reference.ReferenceTimes()
        self._reset_traces()
        self._reset_flags()

    def _ref_args(self, time_ms: int, sector: int | None = None) -> dict:
        """ref_delta_ms/ref_label kwargs for brain calls — empty when the
        reference is just the driver's own bests (original behaviour)."""
        if self.reference.source == "self":
            return {}
        target = (self.reference.sector_target(sector) if sector
                  else self.reference.lap_target())
        if not target:
            return {}
        return {"ref_delta_ms": time_ms - target,
                "ref_label": self.reference.label()}

    def _reset_flags(self):
        self._sc_status = 0
        self._yellow = False
        self._blue = False
        self._last_flag = (None, 0.0)

    def _reset_traces(self):
        self._cur_trace = trace.LapTrace()
        self._best_trace = None
        self._prev_best_trace = None  # what the new best gets explained against
        self._best_trace_valid = False
        self._trace_window = deque(maxlen=4)  # recent full laps, race reference
        self._last_lap_distance = 0.0
        self._sector_start_m = 0.0
        self._live_watcher = trace.LiveZoneWatcher()
        self._last_live_call = 0.0
        self._live_called_zones = set()  # zones already called mid-lap this sector
        self._zone_mentions = {}         # zone key -> monotonic time last spoken

    def handle(self, data: bytes) -> None:
        # Fully paywalled: without an active subscription (or local dev
        # mode) no telemetry is processed — no voice, no dashboard data.
        if self.cloud is not None and not self.cloud.entitled:
            return
        # m_packetFormat is the first uint16 of every F1 header and has been
        # since 2018, so it reads the same regardless of which layout follows
        # — which is precisely what picks the codec for the rest of the bytes.
        if len(data) < formats.FORMAT_FIELD.size:
            log.warning("runt packet, %d bytes — ignored", len(data))
            return
        fmt = formats.FORMAT_FIELD.unpack_from(data, 0)[0]
        codec = formats.codec_for(fmt)
        if codec is None:
            if not self._warned_format:
                self._warned_format = True
                log.warning("packet format %d is not supported — this build "
                            "handles %s", fmt,
                            ", ".join(str(s) for s in formats.SUPPORTED))
            return
        if codec is not self.codec:
            self.codec = codec
            log.info("telemetry format %d (%s) — decoding with the %d codec",
                     fmt, formats.GAME_NAMES.get(fmt, "?"), fmt)

        header = codec.parse_header(data)
        if header is None:
            log.warning("runt packet, %d bytes — ignored", len(data))
            return

        if self._raw_logged < 5:
            self._raw_logged += 1
            log.info("packet: %d bytes, format=%d, id=%d (%s), sessionUID=%d",
                     len(data), header.packet_format, header.packet_id,
                     packets.PACKET_NAMES.get(header.packet_id, "?"),
                     header.session_uid)

        if not codec.size_ok(header.packet_id, len(data)):
            if header.packet_id not in self._warned_sizes:
                self._warned_sizes.add(header.packet_id)
                log.warning("%s packet is %d bytes, the %d spec says %d — "
                            "parser skipping this type; struct layout needs "
                            "checking",
                            packets.PACKET_NAMES.get(header.packet_id, "?"),
                            len(data), codec.FORMAT,
                            codec.expected_size(header.packet_id))
            return

        if self.dash:
            self.dash.touch()

        if header.session_uid != self._session_uid:
            if header.session_uid == self._candidate_uid:
                self._candidate_count += 1
            else:
                self._candidate_uid = header.session_uid
                self._candidate_count = 1
            first = self._session_uid is None
            if not first and self._candidate_count < SESSION_STABLE_PACKETS:
                return  # menu-time UID churn — not a real session change
            self._session_uid = header.session_uid
            self._candidate_uid = None
            self._candidate_count = 0
            self.analyzer = Analyzer()
            self.brain = RaceEngineer()
            if not first:
                self.archive_session()
            self._laps_seen = 0
            self._pending_s3 = None
            self.memory = CornerMemory()
            self._stories = {}
            self._history_announced = False
            self.reference = reference.ReferenceTimes()
            self._reset_traces()
            self._reset_flags()
            if self.dash:
                self.dash.set_laps([])
                self.dash.update(advice=None, sectorStories={}, profile=[],
                                 recurring=None, weakTurns=[])
            if first:
                self._say(self.brain.radio_check())
            else:
                log.info("new session detected — resetting bests and lap history")
                self._say(self.brain.new_session())

        if header.packet_id in PER_CAR_PACKETS:
            spectating = header.player_car_index >= packets.NUM_CARS
            if spectating != self._spectating:
                self._spectating = spectating
                log.info("no player car (index %d) — spectating, coaching "
                         "paused" if spectating else
                         "player car is back (index %d) — coaching resumed",
                         header.player_car_index)
                if self.dash:
                    self.dash.update(spectating=spectating)
            if spectating:
                return

        if header.packet_id == packets.LAP_DATA:
            lap = codec.parse_lap_data(data, header.player_car_index)
            prev_dist = self._last_lap_distance
            self._last_lap_distance = lap.lap_distance
            events = self.state.update_from_lap_data(header, lap)
            for event in events:
                self._on_event(event)
            # Distance rewound without a lap completing: Time Trial restart
            # or flashback — this is a fresh attempt, start a fresh trace.
            if (0 <= lap.lap_distance < prev_dist - 200
                    and not any(isinstance(e, LapComplete) for e in events)):
                self._cur_trace = trace.LapTrace()
                # The map recorder must restart too, or the abandoned
                # fragment gets stitched onto the new attempt and the drawn
                # circuit teleports across the screen.
                self._map_rec.reset()
                self._sector_start_m = 0.0
            if self.dash:
                self.dash.update(
                    lapNo=lap.current_lap_num,
                    currentLapMs=lap.current_lap_time_ms,
                    currentSector=lap.sector,
                    s1Ms=lap.sector1_ms, s2Ms=lap.sector2_ms,
                    lapDistanceM=round(max(0.0, lap.lap_distance), 1))
        elif header.packet_id == packets.EVENT:
            self._on_game_event(codec.parse_event(data), header)
        elif header.packet_id == packets.CAR_STATUS:
            status = codec.parse_car_status(data, header.player_car_index)
            self.state.update_from_car_status(status)
            blue = status.vehicle_fia_flags == packets.FIA_FLAG_BLUE
            if blue and not self._blue:
                self._flag("blue")
            self._blue = blue
            # 2020 has no Car Damage packet — tyre wear rides along in Car
            # Status instead, so pick it up here rather than at CAR_DAMAGE.
            if codec.WEAR_FROM_CAR_STATUS:
                wear = codec.wear_from_car_status(data, header.player_car_index)
                if wear:
                    self._wear_pct = round(sum(wear) / 4, 1)
            if self.dash:
                self.dash.update(
                    compound=VISUAL_COMPOUND_NAMES.get(status.visual_tyre_compound),
                    tyreAge=status.tyres_age_laps,
                    fuelKg=round(status.fuel_in_tank, 2),
                    fuelLaps=round(status.fuel_remaining_laps, 2),
                    wearPct=self._wear_pct)
        elif header.packet_id == packets.SESSION:
            info = codec.parse_session(data)
            self._track_id = info.track_id
            self._track_length = info.track_length
            self._session_type = info.session_type
            self._race_mode = info.session_type in codec.RACE_SESSION_TYPES
            self.brain.race_mode = self._race_mode
            self.brain.quali_mode = (info.session_type
                                     in codec.QUALI_SESSION_TYPES)
            self.reference.set_track(info.track_id)
            self._check_session_flags(codec.parse_session_flags(data))
            if not self._history_announced:
                self._history_announced = True
                prev = driver_db.last_for_track(self._db, info.track_id)
                if prev:
                    self._say(self.brain.track_history_line(prev["best_lap_ms"]))
            if self.dash:
                game = formats.GAME_NAMES.get(codec.FORMAT, "F1")
                track = packets.TRACK_NAMES.get(info.track_id, game)
                session = codec.SESSION_TYPE_NAMES.get(info.session_type, "")
                label = " · ".join(p.upper() for p in (track, session, game) if p)
                self.dash.update(sessionLabel=label, paused=bool(info.game_paused),
                                 trackId=info.track_id,
                                 trackLength=info.track_length,
                                 trackPath=self._track_maps.get(
                                     str(info.track_id)),
                                 reference=self.reference.state())
        elif header.packet_id == packets.CAR_DAMAGE:
            wear = codec.parse_tyre_wear(data, header.player_car_index)
            self._wear_pct = round(sum(wear) / 4, 1)
            if self.dash:
                self.dash.update(wearPct=self._wear_pct)
        elif header.packet_id == packets.MOTION:
            # Build an accurate circuit outline from where the car actually
            # goes, rather than relying on the hand-drawn sketches.
            if self._track_id is not None and str(self._track_id) not in self._track_maps:
                x, _y, z = codec.parse_car_position(data, header.player_car_index)
                self._map_rec.add(self._last_lap_distance, x, z)
        elif header.packet_id == packets.CAR_TELEMETRY:
            tel = codec.parse_car_telemetry(data, header.player_car_index)
            self.state.update_from_telemetry(tel)
            self._cur_trace.add(self._last_lap_distance, tel.speed_kph,
                                tel.throttle, tel.brake)
            zone = self._live_watcher.feed(self._last_lap_distance,
                                           tel.speed_kph, tel.throttle, tel.brake)
            if zone is not None:
                self._maybe_live_corner_call(zone)
            log.debug("telemetry: %d kph, throttle %.2f, brake %.2f, gear %d",
                      tel.speed_kph, tel.throttle, tel.brake, tel.gear)
        elif header.packet_id == packets.SESSION_HISTORY:
            self._check_history(data, header, codec)
        elif header.packet_id == packets.TIME_TRIAL:
            tt = codec.parse_time_trial(data)
            if tt is None:  # format predates the Time Trial packet
                return
            self.reference.update_rival(tt.rival)
            if self.dash:
                self.dash.update(reference=self.reference.state())

    def _on_event(self, event) -> None:
        if isinstance(event, SectorComplete):
            result = self.analyzer.on_sector(event)
            best = self.analyzer.best_sectors[result.sector]
            print(f"[lap {result.lap_num}] S{result.sector} {fmt_ms(result.time_ms)}"
                  f"  delta {fmt_delta(result.delta_ms)}"
                  f"  best {fmt_ms(best) if best else '—'}"
                  f"{'  ** personal best' if result.is_best and result.delta_ms is not None else ''}"
                  f"{'  (lap invalid)' if result.lap_invalid else ''}")
            if result.sector == 3:
                findings = self._zone_findings(d1=self._cur_trace.max_dist)
                self._record_story(result, findings)
                if findings:
                    self.memory.note_sector(result.lap_num, findings)
                self._pending_s3 = result  # spoken with the lap line
            else:
                findings = self._zone_findings()
                self._record_story(result, findings)
                verdict = (self.memory.note_sector(result.lap_num, findings)
                           if findings else None)
                worst = max((f for f in findings if not f.gain),
                            key=lambda f: f.score(), default=None)
                already_called = (worst is not None
                                  and round(worst.onset_m / 50) * 50
                                  in self._live_called_zones)
                corner = (worst if self._corner_gate(result)
                          and not already_called else None)
                self.reference.update_own(self.analyzer.best_sectors)
                line = self.brain.sector_line(
                    result, corner, self._track_id, verdict,
                    **self._ref_args(result.time_ms, result.sector))
                if line:
                    if worst is not None and (corner is not None or (
                            verdict and verdict.kind in ("repeat", "streak"))):
                        self._zone_mentions[round(worst.onset_m / 50) * 50] = \
                            time.monotonic()
                    self._say(line)
                self._live_called_zones.clear()
                self._sector_start_m = self._last_lap_distance
        elif isinstance(event, LapComplete):
            result = self.analyzer.on_lap(event)
            s1, s2, s3 = event.sector_times_ms
            print(f"[lap {result.lap_num}] LAP {fmt_ms(result.lap_time_ms)}"
                  f"  ({fmt_ms(s1)} / {fmt_ms(s2)} / {fmt_ms(s3)})"
                  f"  delta {fmt_delta(result.delta_ms)}"
                  f"{'  ** new best lap' if result.is_best and result.delta_ms is not None else ''}"
                  f"{'  INVALID' if result.invalid else ''}")
            # Why was it faster? Compare the new best against the lap it beat.
            gains = None
            if (result.is_best and self._best_trace is not None
                    and self._cur_trace.dist and self._cur_trace.dist[0] < 100):
                gains = trace.compare_zones(self._cur_trace, self._best_trace,
                                            0, self._cur_trace.max_dist)
            bests = self.analyzer.best_sectors
            theoretical = sum(bests.values()) if all(bests.values()) else None
            self.reference.update_own(bests)
            if self._pending_s3 is not None:
                self._say(self.brain.lap_line(
                    self._pending_s3, result, self.state, gains, theoretical,
                    self._track_id, **self._ref_args(result.lap_time_ms)))
                self._pending_s3 = None
            self._laps_seen += 1
            if self.dash:
                self.dash.add_lap(event.lap_num, s1, s2, s3, {
                    "invalid": event.invalid,
                    "wear": self._wear_pct,
                    "fuel": self.state.fuel_in_tank,
                    "yellow": self._yellow,
                })
            # Promote this lap's trace if it's the fastest fully-observed
            # lap, then start a fresh trace for the new lap. A trace that
            # starts mid-lap (listener joined partway round) must never
            # become the reference — it can't match early-lap corners.
            # Valid laps always outrank invalid ones (Time Trial deletes
            # most attempts, so an invalid lap may be the best we have).
            self._cur_trace.lap_time_ms = event.lap_time_ms
            full_lap = self._cur_trace.dist and self._cur_trace.dist[0] < 100
            if full_lap:
                self._trace_window.append(self._cur_trace)
                valid = not event.invalid
                better = (self._best_trace is None
                          or event.lap_time_ms < self._best_trace.lap_time_ms)
                if ((valid and (better or not self._best_trace_valid))
                        or (not valid and not self._best_trace_valid and better)):
                    self._prev_best_trace = self._best_trace
                    self._best_trace = self._cur_trace
                    self._best_trace_valid = valid
            if full_lap and self._map_rec.complete(self._track_length):
                entry = track_map.record(self._track_maps, self._track_id,
                                         self._map_rec.points,
                                         self._track_length)
                if entry and self.dash:
                    self.dash.update(trackPath=entry)
            elif full_lap and self._map_rec.samples:
                log.debug("track map not recorded: %d samples, starts at %.0fm, "
                          "reaches %.0fm of %s, largest gap %.0fm",
                          len(self._map_rec.samples),
                          self._map_rec.samples[0][0],
                          self._map_rec.samples[-1][0], self._track_length,
                          self._map_rec.gaps())
            self._map_rec.reset()
            self._cur_trace = trace.LapTrace()
            self._sector_start_m = 0.0
            self._live_called_zones.clear()
            self._update_intel()

    def speak_recap(self) -> None:
        """Triggered by the dashboard's Recap button (HTTP thread)."""
        laps = self.dash.snapshot()["laps"] if self.dash else []
        prev = (driver_db.last_for_track(self._db, self._track_id)
                if self._track_id is not None else None)
        line = self.brain.recap_words(
            analysis.session_recap(laps), self.memory.top_issue(),
            self._track_id, prev["best_lap_ms"] if prev else None,
            self.memory.worst_zones(3))
        self._say(line)

    def _flag(self, kind, seconds=None):
        now = time.monotonic()
        if kind == self._last_flag[0] and now - self._last_flag[1] < 5:
            return  # the game re-sends events; call it once
        self._last_flag = (kind, now)
        line = self.brain.flag_line(kind, seconds)
        if line:
            print(f'  🚩 "{line}"')
            self.announcer.say(line, priority=True)

    def _check_session_flags(self, flags) -> None:
        yellow = flags.yellow_zones > 0
        if yellow != self._yellow:
            self._yellow = yellow
            # Suppress "clear" chatter under SC — the SC calls own that story.
            if yellow:
                self._flag("yellow")
            elif self._sc_status == packets.SC_NONE:
                self._flag("clear")
        sc = flags.safety_car_status
        if sc != self._sc_status:
            prev, self._sc_status = self._sc_status, sc
            if sc == packets.SC_FULL:
                self._flag("sc")
            elif sc == packets.SC_VIRTUAL:
                self._flag("vsc")
            elif sc == packets.SC_NONE and prev in (packets.SC_FULL,
                                                    packets.SC_VIRTUAL):
                self._flag("green")

    def _on_game_event(self, ev, header) -> None:
        if ev.code == "RDFL":
            self._flag("red")
        elif ev.code == "CHQF":
            self._flag("chequered")
        elif ev.code == "SCAR":
            scar = packets.parse_safety_car_event(ev)
            if scar.event_type == 1:  # returning — status change covers the rest
                self._flag("sc_in")
        elif ev.code == "PENA":
            pen = packets.parse_penalty_event(ev)
            if pen.vehicle_idx == header.player_car_index:
                if pen.penalty_type == 0:
                    self._flag("drive_through")
                elif pen.penalty_type == 1:
                    self._flag("stop_go")
                elif pen.penalty_type == 4 and pen.time not in (0, 255):
                    self._flag("time_penalty", pen.time)

    def _maybe_live_corner_call(self, zone) -> None:
        """Immediate coaching right after a corner, not at the sector line.
        High-confidence, significant losses only — and rationed."""
        reference = self._reference_trace()
        if reference is None:
            return
        finding = trace.assess_zone(zone, reference)
        if finding is None or finding.gain or finding.confidence != "high":
            return
        if self.brain.quali_mode:
            return  # qualifying: minimal interruptions
        now = time.monotonic()
        key = round(finding.onset_m / 50) * 50
        if now - self._last_live_call < LIVE_CORNER_COOLDOWN_S:
            return
        if now - self._zone_mentions.get(key, -999) < 8:
            return  # a sector line just named this corner — don't repeat it
        self._last_live_call = now
        self._live_called_zones.add(key)
        self._zone_mentions[key] = now
        self._say(self.brain.live_corner_line(finding, self._track_id))

    def _reference_trace(self):
        # Race mode compares against your recent stint (fastest of the last
        # few laps), not the session-best hotlap.
        if self._race_mode:
            return (min(self._trace_window, key=lambda t: t.lap_time_ms)
                    if self._trace_window else None)
        return self._best_trace

    def _zone_findings(self, d1=None) -> list:
        reference = self._reference_trace()
        if reference is None:
            return []
        return trace.compare_zones(self._cur_trace, reference,
                                   self._sector_start_m,
                                   d1 if d1 is not None else self._last_lap_distance)

    def _corner_gate(self, result) -> bool:
        """Only speak corner detail when the sector loss is worth air time."""
        if self._race_mode:
            return (result.stint_delta_ms is not None
                    and result.stint_delta_ms >= config.RACE_CALLOUT_MIN_DELTA_MS)
        return (result.delta_ms is not None
                and result.delta_ms >= CORNER_NOTE_MIN_DELTA_MS)

    def _record_story(self, result, findings) -> None:
        """Per-sector lost/gained bullets for the dashboard."""
        if not self.dash:
            return
        losses = sorted((f for f in findings if not f.gain),
                        key=lambda f: f.score(), reverse=True)[:2]
        gains = sorted((f for f in findings if f.gain),
                       key=lambda f: f.score(), reverse=True)[:2]
        entry = {
            "lap": result.lap_num,
            "lost": [{"label": trace.turn_label(self._track_id, f.onset_m),
                      "what": STORY_CAUSE_WORDS.get(f.cause, f.cause)}
                     for f in losses],
            "gained": [{"label": trace.turn_label(self._track_id, f.onset_m),
                        "what": STORY_CAUSE_WORDS.get(f.cause, f.cause)}
                       for f in gains],
        }
        self._stories[result.sector] = entry
        self.dash.update(sectorStories=dict(self._stories))

    def _update_intel(self) -> None:
        """Post-lap: advice line, recurring issue, driver-style chips."""
        if not self.dash:
            return
        advice = recurring = None
        top = self.memory.top_issue()
        if top:
            onset_m, cause, avg, laps = top
            label = trace.turn_label(self._track_id, onset_m)
            advice = self.brain.ADVICE.get(cause, "Tidy up {}").format(label)
            recurring = {"label": label,
                         "what": STORY_CAUSE_WORDS.get(cause, cause),
                         "laps": laps, "avg": avg,
                         "unit": "m" if cause == "braked_early" else "kph"}
        weak = [{"label": trace.turn_label(self._track_id, onset_m),
                 "onsetM": onset_m,
                 "what": STORY_CAUSE_WORDS.get(cause, cause),
                 "laps": laps, "avg": avg,
                 "unit": "m" if cause == "braked_early" else "kph",
                 "severity": "red" if i == 0 and laps >= 3 else "amber"}
                for i, (onset_m, cause, avg, laps)
                in enumerate(self.memory.worst_zones(5))]
        recap = analysis.session_recap(self.dash.snapshot()["laps"])
        chips = self.memory.profile_chips(
            recap["consistency_ms"] if recap else None,
            recap["laps"] if recap else 0)
        self.dash.update(advice=advice, recurring=recurring, profile=chips,
                         weakTurns=weak)

    def archive_session(self) -> None:
        """Persist a session summary to the long-term driver database."""
        if not self.dash or self._track_id is None:
            return
        laps = self.dash.snapshot()["laps"]
        recap = analysis.session_recap(laps)
        record = driver_db.make_record(self._track_id, self._session_type,
                                       recap, self.memory.top_issue())
        if record:
            record["track_name"] = packets.TRACK_NAMES.get(self._track_id)
            driver_db.append(record)
            self._db = driver_db.load()

    def _say(self, line: str) -> None:
        print(f'  🎧 "{line}"')
        self.announcer.say(line)

    def _check_history(self, data: bytes, header, codec) -> None:
        """Backfill laps recorded before we started listening, and cross-check
        our tracked bests against the game's Session History."""
        history = codec.parse_session_history(data)
        if history is None:  # format predates the Session History packet
            return
        if history.car_idx != header.player_car_index or not history.laps:
            return

        complete = [(i + 1, l) for i, l in enumerate(history.laps)
                    if l.lap_time_ms > 0 and l.sector1_ms > 0
                    and l.sector2_ms > 0 and l.sector3_ms > 0]
        # Only when joining mid-session: after that our own tracking is
        # authoritative, and seeding continuously would race lap completion.
        if complete and self._laps_seen == 0:
            log.info("joined mid-session: backfilling %d laps from Session History",
                     len(complete))
            self._laps_seen = len(complete)
            for _, l in complete:
                # The game counts sector bests from laps that were later
                # invalidated — honour its per-sector valid flags.
                self.analyzer.seed_bests(
                    l.sector1_ms if l.valid_flags & 0x02 else 0,
                    l.sector2_ms if l.valid_flags & 0x04 else 0,
                    l.sector3_ms if l.valid_flags & 0x08 else 0,
                    l.lap_time_ms if l.valid_flags & 0x01 else 0)
            if self.dash:
                self.dash.set_laps([
                    {"lap": n, "sectors": [l.sector1_ms, l.sector2_ms, l.sector3_ms],
                     "invalid": not l.valid_flags & 0x01}
                    for n, l in complete])

        now = time.monotonic()
        if now - self._last_history_check < 10:
            return
        self._last_history_check = now
        game_best_s2 = None
        if 0 < history.best_sector2_lap_num <= len(history.laps):
            game_best_s2 = history.laps[history.best_sector2_lap_num - 1].sector2_ms
        # A provisional best (from a deleted lap, with nothing valid yet) is
        # expected to differ: the game only counts valid sectors.
        ours = (self.analyzer.best_sectors[2]
                if self.analyzer.best_sector_valid[2] else None)
        if game_best_s2 and ours and game_best_s2 != ours:
            pair = (game_best_s2, ours)
            if pair != getattr(self, "_warned_s2", None):
                self._warned_s2 = pair
                log.warning("best S2 mismatch: game says %s, we tracked %s",
                            fmt_ms(game_best_s2), fmt_ms(ours))
        else:
            log.debug("session history check OK (%d laps on record)",
                      history.num_laps)


def run_raw(sock: socket.socket) -> None:
    """Phase 1: prove packets arrive — log size + header fields, nothing else."""
    print("Raw mode: logging every packet header. Ctrl-C to stop.")
    count = 0
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        count += 1
        codec = (formats.codec_for(formats.FORMAT_FIELD.unpack_from(data, 0)[0])
                 if len(data) >= formats.FORMAT_FIELD.size else None)
        h = codec.parse_header(data) if codec else None
        if h is None:
            print(f"#{count} from {addr[0]}: {len(data)} bytes "
                  f"(unsupported format or too short for header)")
            continue
        print(f"#{count} from {addr[0]}: {len(data)} bytes  format={h.packet_format}"
              f"  id={h.packet_id} ({packets.PACKET_NAMES.get(h.packet_id, '?')})"
              f"  sessionUID={h.session_uid}  sessionTime={h.session_time:.1f}s"
              f"  playerCarIdx={h.player_car_index}")


def setup_logging(verbose: bool):
    """Console logging (as before) plus a rotating file in APP_DIR/logs so a
    tester can send a log file instead of screenshotting the Terminal.
    Returns the log path (or None if the file couldn't be opened)."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
                        datefmt="%H:%M:%S")
    log_path = None
    try:
        log_dir = config.APP_DIR / "logs"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / "pitwall.log"
        fh = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
        logging.getLogger().addHandler(fh)
        logging.getLogger("pitwall").info(
            "=== PitWall started (%s) ===", time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        logging.getLogger("pitwall").warning("could not open log file", exc_info=True)

    # Route uncaught exceptions (main thread + worker threads) into the log
    # so crashes leave a trace on disk, not just a fleeting Terminal message.
    crash_log = logging.getLogger("crash")

    def excepthook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        crash_log.critical("UNCAUGHT EXCEPTION\n%s",
                           "".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)  # keep the Terminal traceback too

    def thread_excepthook(args):
        if issubclass(args.exc_type, KeyboardInterrupt):
            return
        crash_log.critical("UNCAUGHT EXCEPTION in thread %s\n%s", args.thread.name,
                           "".join(traceback.format_exception(
                               args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook
    return log_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="F1 voice race engineer (UDP formats 2020-2025)")
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--raw", action="store_true",
                        help="Phase 1: log raw packet headers only, no parsing")
    parser.add_argument("--no-tts", action="store_true",
                        help="console output only, no voice")
    parser.add_argument("--tts-engine", choices=["kokoro", "say"],
                        default=config.TTS_ENGINE,
                        help="kokoro = natural neural voice (bundled), "
                             "say = built-in fallback")
    parser.add_argument("--voice", default=config.TTS_VOICE)
    parser.add_argument("--rate", type=int, default=config.TTS_RATE)
    parser.add_argument("--no-dashboard", action="store_true",
                        help="don't serve the pit-wall web dashboard")
    parser.add_argument("--no-browser", action="store_true",
                        help="don't auto-open the dashboard in a browser tab")
    parser.add_argument("--dashboard-port", type=int, default=config.DASHBOARD_PORT)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    log_path = setup_logging(args.verbose)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((config.BIND_IP, args.port))
    sock.settimeout(1.0)
    print(f"Listening for F1 25 telemetry on UDP {config.BIND_IP}:{args.port} …")
    if log_path:
        print(f"Logs (send this if something breaks): {log_path}")

    if args.raw:
        try:
            run_raw(sock)
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    # Frozen builds must always run against the license backend — LOCAL DEV
    # MODE is a source-checkout convenience only, never a shipped fallback.
    if config.IS_FROZEN and not (config.SUPABASE_URL and config.SUPABASE_ANON_KEY):
        print("This build is missing its licensing configuration and cannot "
              "run.\nRe-download PitWall, or contact Jordan for a fresh build.")
        sys.exit(1)

    cloud = cloud_mod.Cloud()
    if not cloud.configured:
        print("LOCAL DEV MODE — no auth/paywall (set SUPABASE_URL to enable).")
    elif cloud.entitled:
        who = f" ({cloud.email})" if cloud.email else ""
        print(f"License OK{who} — verifying with the server in the background.")
    else:
        print("Subscription required — sign in via the dashboard. "
              "(If you're offline, connect once to activate.)")

    dash = None
    if not args.no_dashboard:
        dash = Dashboard()
        dash.cloud = cloud
        dash.start(args.dashboard_port)
        dashboard_url = f"http://127.0.0.1:{args.dashboard_port}"
        print(f"Pit wall dashboard: {dashboard_url}")
        if not args.no_browser:
            # No window of our own (background listener) — open the
            # dashboard automatically so there's something to look at.
            try:
                webbrowser.open(dashboard_url)
            except Exception:
                pass

    announcer = make_announcer(not args.no_tts, args.tts_engine, args.voice, args.rate)
    engineer = Engineer(announcer, dash, cloud)
    if dash:
        dash.on_recap = engineer.speak_recap
    handler_errors = 0
    try:
        while True:
            try:
                data, _ = sock.recvfrom(2048)
            except socket.timeout:
                continue
            try:
                engineer.handle(data)
            except Exception:
                # One bad packet or analysis edge case must not end the
                # session (and lose the driver's data). Log it and carry on;
                # throttle so a persistent bug can't fill the disk.
                handler_errors += 1
                if handler_errors <= 50 or handler_errors % 500 == 0:
                    log.exception("error handling packet (#%d) — continuing",
                                  handler_errors)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        engineer.archive_session()
        announcer.close()
        if dash:
            dash.stop()


if __name__ == "__main__":
    main()
