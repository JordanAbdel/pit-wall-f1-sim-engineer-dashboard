"""Race engineer persona — analysis results in, pit-wall radio lines out.

Register: real pit-wall radio. Clipped, delta-first, no filler, no
coaching-speak. Triage before speaking — a delta under
config.CALLOUT_MIN_DELTA_MS gets silence, not a forced comment. Hard
cap of two short sentences per callout. Warmth is reserved for a
clearly strong new best, and even then it's two flat words.

This module owns tone and phrasing only; the numbers come from
analysis.py, corner findings from trace.py, live context from state.py.
"""
import random

import config
import trace
from analysis import LapResult, SectorResult

SECTOR_WORDS = {1: "one", 2: "two", 3: "three"}
BIG_MOMENT_MS = 5000   # loss so large it's an off, not a delta worth reciting
STRONG_BEST_MS = 300   # a best by this much earns two words of warmth
MIN_TEL_SAMPLES = 40   # don't comment on driving stats from a handful of samples


def _delta_words(delta_ms: int) -> str:
    """Broadcast-style magnitude, one decimal: 'point 3', '1 point 2'."""
    seconds = abs(delta_ms) / 1000
    whole = int(seconds)
    tenth = round((seconds - whole) * 10)
    if tenth == 10:
        whole, tenth = whole + 1, 0
    if whole == 0:
        return f"point {tenth}"
    return f"{whole} point {tenth}"


def _lap_time_words(ms: int) -> str:
    """83421 -> 'one 23.4' (TTS reads it as 'one twenty-three point four')."""
    minutes = ms // 60_000
    seconds = (ms % 60_000) / 1000
    sec_int = int(seconds)
    tenth = int(round((seconds - sec_int) * 10)) % 10
    sec_words = f"oh {sec_int}" if sec_int < 10 else str(sec_int)
    if minutes == 0:
        return f"{seconds:.1f}"
    return f"{'one' if minutes == 1 else minutes} {sec_words} point {tenth}"


class RaceEngineer:
    # Imperative coaching per cause — used for streak calls, the
    # dashboard advice line, and the debrief recommendation.
    ADVICE = {
        "braked_early": "Brake later into {}",
        "overslowed": "Carry more speed through {}",
        "slow_exit": "Earlier on the throttle out of {}",
    }

    def __init__(self):
        self._rng = random.Random()
        self._last_pick = {}
        self._next_tyre_note = 8
        self._trap_announced = 0.0
        self._fuel_thresholds = [3.0, 1.5]
        self.race_mode = False   # set from the Session packet (race vs. hotlap)
        self.quali_mode = False  # qualifying: minimal interruptions

    def _pick(self, key: str, options: list) -> str:
        """Never repeat the previous phrasing for the same situation."""
        last = self._last_pick.get(key)
        choices = [o for o in options if o != last] or options
        choice = self._rng.choice(choices)
        self._last_pick[key] = choice
        return choice

    # ---- flag calls: fixed, instant, no variation — safety radio ----
    FLAG_CALLS = {
        "yellow": "Yellow flag.",
        "clear": "Track clear, green flag.",
        "red": "Red flag, red flag.",
        "sc": "Safety car deployed.",
        "vsc": "Virtual safety car deployed.",
        "sc_in": "Safety car in this lap.",
        "green": "Green flag, green flag.",
        "blue": "Blue flag, car behind on a fast lap.",
        "chequered": "Chequered flag.",
        "drive_through": "Drive through penalty.",
        "stop_go": "Stop go penalty.",
    }

    def flag_line(self, kind: str, seconds: int | None = None) -> str | None:
        if kind == "time_penalty" and seconds:
            return f"Time penalty, {seconds} seconds."
        return self.FLAG_CALLS.get(kind)

    # ---- radio calls ----
    def radio_check(self) -> str:
        return self._pick("radio", [
            "Radio check.",
            "Radio check — telemetry live.",
        ])

    def new_session(self) -> str:
        return self._pick("new_session", [
            "New session. Times reset.",
            "New session, bests cleared.",
        ])

    # ---- corner detail (one clipped sentence, data-backed only) ----
    def _corner_words(self, finding, track_id) -> str:
        name = trace.turn_name(track_id, finding.onset_m)
        soft = "" if finding.confidence == "high" else "Looks like "
        if finding.cause == "overslowed":
            line = f"overslowed {name} — {abs(finding.min_speed_diff)} k down at apex."
        elif finding.cause == "braked_early":
            metres = round(abs(finding.onset_diff_m) / 5) * 5
            line = f"braking early into {name}, {metres} metres."
        else:
            line = f"slow exit at {name}, throttle sooner."
        if soft:
            return soft + line
        return line[0].upper() + line[1:]

    SHORT_ADVICE = {"braked_early": "brake later",
                    "overslowed": "carry more speed",
                    "slow_exit": "earlier on the throttle"}

    def live_corner_line(self, finding, track_id) -> str:
        """Immediate call as the car exits a corner it just lost time in.
        One sentence — the driver is mid-lap."""
        name = trace.turn_name(track_id, finding.onset_m)
        if finding.cause == "braked_early":
            metres = round(abs(finding.onset_diff_m) / 5) * 5
            return self._pick("live_early", [
                f"Braking early into {name} — go {metres} metres deeper.",
                f"You gave up {metres} metres on the brakes at {name}. Later next time.",
            ])
        if finding.cause == "overslowed":
            k = abs(finding.min_speed_diff)
            return self._pick("live_overslow", [
                f"Overslowed {name}, {k} down — carry more in next lap.",
                f"{k} k down through {name}. Trust it, more apex speed.",
            ])
        return self._pick("live_exit", [
            f"Lazy exit off {name} — earlier on the throttle.",
            f"Lost the drive out of {name}. Power down sooner.",
        ])

    def _memory_words(self, finding, verdict, track_id) -> str:
        """Repeat-mistake phrasing — the engineer remembers."""
        name = trace.turn_name(track_id, finding.onset_m)
        if verdict.kind == "repeat":
            return f"Same again at {name}."
        fix = self.SHORT_ADVICE.get(finding.cause, "tidy it up")
        return f"That's {verdict.streak} laps running at {name} — {fix}."

    def _driving_note(self, r: SectorResult) -> str | None:
        stats, best = r.stats, r.best_stats
        if (stats is None or best is None
                or stats.samples < MIN_TEL_SAMPLES or best.samples < MIN_TEL_SAMPLES):
            return None
        speed_diff = stats.top_speed - best.top_speed
        if speed_diff <= -5:
            return f"Down {abs(speed_diff)} on top speed."
        if stats.brake_count > best.brake_count:
            return "Extra brake application."
        if stats.full_throttle_pct - best.full_throttle_pct <= -5:
            return "Less time on full throttle."
        return None

    # ---- sectors 1 and 2 (sector 3 is folded into the lap call) ----
    def sector_line(self, r: SectorResult, corner=None, track_id=None,
                    verdict=None) -> str | None:
        s = SECTOR_WORDS[r.sector]
        if self.race_mode:
            return self._race_sector_line(r, s, corner, track_id)
        if r.delta_ms is None:
            return None  # no baseline yet — nothing worth air time
        threshold = config.CALLOUT_MIN_DELTA_MS * (2 if self.quali_mode else 1)
        fixed = (f"Better at {trace.turn_name(track_id, verdict.fixed_onset)}."
                 if verdict is not None and verdict.fixed_onset is not None
                 else None)
        if r.is_best:
            if fixed:
                return f"Purple, sector {s}. {fixed}"
            if abs(r.delta_ms) >= config.CALLOUT_MIN_DELTA_MS:
                return f"Purple, sector {s}. Minus {_delta_words(r.delta_ms)}."
            return f"Purple, sector {s}."
        if r.delta_ms < threshold:
            return fixed  # on pace — only an earned "better there" gets air
        if r.delta_ms >= BIG_MOMENT_MS:
            return self._pick("off", [
                f"Off in sector {s}. Lap's gone.",
                f"Big moment, sector {s}. Scrap the lap.",
            ])
        line = f"Plus {_delta_words(r.delta_ms)}, sector {s}."
        if corner is not None and verdict is not None and verdict.kind in ("repeat", "streak"):
            return f"{line} {self._memory_words(corner, verdict, track_id)}"
        detail = (self._corner_words(corner, track_id) if corner is not None
                  else self._driving_note(r))
        return f"{line} {detail}" if detail else line

    def _race_sector_line(self, r: SectorResult, s: str, corner, track_id) -> str | None:
        """Race reference is stint pace, not the session-best hotlap.
        Normal race variance gets silence; purples still get called."""
        if r.is_best and r.delta_ms is not None:
            return f"Purple, sector {s}."
        sd = r.stint_delta_ms
        if sd is None:
            return None
        if sd >= BIG_MOMENT_MS:
            return self._pick("off", [
                f"Off in sector {s}. Keep it together.",
                f"Big moment, sector {s}.",
            ])
        if sd >= config.RACE_CALLOUT_MIN_DELTA_MS:
            line = f"Plus {_delta_words(sd)} on stint pace, sector {s}."
            if corner is not None:
                return f"{line} {self._corner_words(corner, track_id)}"
            return line
        return None

    def _gain_words(self, gains, track_id) -> str | None:
        """Why the lap was faster — the top contributors, stated as facts."""
        real = sorted((g for g in gains if g.gain),
                      key=lambda g: g.score(), reverse=True)[:2]
        if not real:
            return None
        bits = []
        for g in real:
            name = trace.turn_name(track_id, g.onset_m)
            if g.cause == "carried_speed":
                bits.append(f"carried {g.min_speed_diff} k more through {name}")
            elif g.cause == "later_brakes":
                bits.append(f"later on the brakes into {name}")
            else:
                bits.append(f"earlier on the power out of {name}")
        joined = ", and ".join(bits)
        return joined[0].upper() + joined[1:] + "."

    # ---- lap completion: at most two short sentences (three for a
    # new best with an explanation — that's a deliberate moment) ----
    def lap_line(self, s3: SectorResult, lap: LapResult, session=None,
                 gains=None, theoretical_ms=None, track_id=None) -> str:
        if self.race_mode:
            return self._race_lap_line(lap, session)
        s3_part = None
        if s3.delta_ms is not None:
            if s3.is_best:
                s3_part = "Purple, sector three."
            elif s3.delta_ms >= BIG_MOMENT_MS:
                s3_part = "Off in the last sector."
            elif s3.delta_ms >= config.CALLOUT_MIN_DELTA_MS:
                s3_part = f"Plus {_delta_words(s3.delta_ms)}, sector three."

        # A plain s3 delta that matches the lap delta says nothing the lap
        # call won't — drop it rather than read the same number twice.
        if (s3_part and not s3.is_best and lap.delta_ms is not None
                and not lap.is_best and s3.delta_ms < BIG_MOMENT_MS
                and abs(s3.delta_ms - lap.delta_ms) < config.CALLOUT_MIN_DELTA_MS):
            s3_part = None

        tw = _lap_time_words(lap.lap_time_ms)
        twc = tw[0].upper() + tw[1:]
        if lap.invalid:
            verdict = f"{twc}. Lap deleted — track limits."
        elif lap.delta_ms is None:
            verdict = f"Baseline lap, {tw}."
        elif lap.is_best:
            explanation = self._gain_words(gains, track_id) if gains else None
            if explanation:
                verdict = f"New best, {tw}. {explanation}"
                if (theoretical_ms is not None
                        and theoretical_ms <= lap.lap_time_ms - STRONG_BEST_MS):
                    verdict += (f" Stack your best sectors and it's a "
                                f"{_lap_time_words(theoretical_ms)}.")
                s3_part = None  # the explanation is the story
            elif lap.delta_ms <= -STRONG_BEST_MS and s3_part is None:
                verdict = f"New best, {tw}. Good lap."
            else:
                verdict = f"New best, {tw}."
        elif lap.delta_ms < config.CALLOUT_MIN_DELTA_MS:
            verdict = f"{twc}, level with your best."
        elif s3_part is None and lap.worst_sector:
            verdict = (f"Plus {_delta_words(lap.delta_ms)}, most of it sector "
                       f"{SECTOR_WORDS[lap.worst_sector]}. {twc}.")
        else:
            verdict = f"Plus {_delta_words(lap.delta_ms)}. {twc}."

        if s3_part:
            return f"{s3_part} {verdict}"
        note = None if self.quali_mode else self._context_note(lap, session)
        if note and verdict.count(". ") == 0:  # keep the two-sentence cap
            return f"{verdict} {note}"
        return verdict

    def _race_lap_line(self, lap: LapResult, session) -> str:
        """Race radio: position, time, then the one thing that matters —
        pace drift, the gap ahead, or a tyre/fuel fact."""
        tw = _lap_time_words(lap.lap_time_ms)
        pos = getattr(session, "car_position", None) if session else None
        gap_ms = getattr(session, "gap_ahead_ms", None) if session else None

        if (lap.is_best and lap.delta_ms is not None
                and lap.delta_ms <= -config.CALLOUT_MIN_DELTA_MS):
            lead = f"Fastest lap, {tw}."
        elif pos:
            lead = f"P{pos}, {tw}."
        else:
            lead = f"{tw[0].upper() + tw[1:]}."

        sd = lap.stint_delta_ms
        if sd is not None and sd >= config.RACE_CALLOUT_MIN_DELTA_MS and not lap.is_best:
            second = f"Pace dropping — plus {_delta_words(sd)} on your stint."
        elif pos and pos > 1 and gap_ms and 0 < gap_ms < 60_000:
            second = f"Gap ahead, {_delta_words(gap_ms)}."
        else:
            second = self._context_note(lap, session)
        return f"{lead} {second}" if second else lead

    # ---- session debrief: deliberate summary, may run a few sentences ----
    def recap_words(self, r: dict | None, recurring=None, track_id=None,
                    prev_best_ms=None, weak_zones=None) -> str:
        if r is None or r["laps"] == 0:
            return "Nothing on the board yet."
        parts = [f"{r['laps']} laps on the board.",
                 f"Best lap, {_lap_time_words(r['best_lap_ms'])}."]
        remaining = r["best_lap_ms"] - r["theoretical_ms"]
        if remaining > config.CALLOUT_MIN_DELTA_MS:
            parts.append(f"Theoretical, {_lap_time_words(r['theoretical_ms'])} — "
                         f"{_delta_words(remaining)} still on the table.")
        gap = r["weakest_gap_ms"]
        if gap >= config.CALLOUT_MIN_DELTA_MS:
            parts.append(f"Most time's going in sector "
                         f"{SECTOR_WORDS[r['weakest_sector']]} — "
                         f"{_delta_words(gap)} a lap. Strongest is sector "
                         f"{SECTOR_WORDS[r['strongest_sector']]}.")
        else:
            parts.append("Sectors are even.")
        CAUSE_WORDS = {"braked_early": "braking early",
                       "overslowed": "apex speed",
                       "slow_exit": "slow exit"}
        if weak_zones and len(weak_zones) >= 2:
            bits = [f"{trace.turn_name(track_id, onset_m)}, "
                    f"{CAUSE_WORDS.get(cause, cause)}"
                    for onset_m, cause, avg, laps in weak_zones[:3]]
            parts.append("Biggest losses: " + "; ".join(bits) + ".")
            top_onset, top_cause, top_avg, top_laps = weak_zones[0]
            fix = self.SHORT_ADVICE.get(top_cause, "tidy it up")
            unit = "metres" if top_cause == "braked_early" else "k"
            parts.append(f"Practice focus: {fix} at "
                         f"{trace.turn_name(track_id, top_onset)} — "
                         f"{top_laps} laps, about {top_avg} {unit}.")
        elif recurring is not None:
            onset_m, cause, avg, laps = recurring
            name = trace.turn_name(track_id, onset_m)
            what = CAUSE_WORDS.get(cause, cause)
            unit = "metres" if cause == "braked_early" else "k"
            fix = self.SHORT_ADVICE.get(cause, "tidy it up")
            parts.append(f"Recurring issue: {what} at {name} — {laps} laps, "
                         f"about {avg} {unit}. Practice focus: {fix} there.")
        improvement = r.get("improvement_ms")
        if improvement is not None and improvement >= 300:
            parts.append(f"Pace came on {_delta_words(improvement)} "
                         f"over the session.")
        sd = r["consistency_ms"]
        if sd is not None:
            parts.append("Consistency's good." if sd < 300 else
                         f"Consistency, plus or minus {_delta_words(sd)}.")
        if prev_best_ms is not None:
            diff = r["best_lap_ms"] - prev_best_ms
            if diff <= -config.CALLOUT_MIN_DELTA_MS:
                parts.append(f"{_delta_words(diff).capitalize()} up on your "
                             f"last session here.")
            elif diff >= config.CALLOUT_MIN_DELTA_MS:
                parts.append(f"Still {_delta_words(diff)} off your last "
                             f"session here.")
        return " ".join(parts)

    def track_history_line(self, best_ms: int) -> str:
        return f"Last time here, best was a {_lap_time_words(best_ms)}."

    def _context_note(self, lap: LapResult, session) -> str | None:
        """One factual status line, priority: trap speed, tyres, fuel."""
        if session is None:
            return None
        trap_kph = session.speed_trap_kph or 0
        if trap_kph and lap.lap_num >= 2 and trap_kph >= self._trap_announced + 3:
            self._trap_announced = trap_kph
            return f"Best trap speed, {round(trap_kph)}."
        age = session.tyre_age_laps
        if age is not None and age >= self._next_tyre_note:
            self._next_tyre_note = age + 4
            return f"Tyres at {age} laps."
        fuel = session.fuel_remaining_laps
        if (fuel is not None and self._fuel_thresholds
                and 0 < fuel < self._fuel_thresholds[0]):
            self._fuel_thresholds.pop(0)
            return f"Fuel at {fuel:.1f} laps."
        return None
