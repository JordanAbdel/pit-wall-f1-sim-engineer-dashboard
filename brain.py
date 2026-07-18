"""Race engineer persona — analysis results in, spoken coaching out.

Register: a human race engineer, not a radio robot. Plain sentences that
say what happened, why, and what to do about it: "You lost about three
tenths in sector one, mostly at La Source — you braked around fifteen
metres too early. Go deeper on the brakes next time." No jargon: no
"purple", no bare "k", numbers spoken the way a person would say them.

Triage still applies — a delta under config.CALLOUT_MIN_DELTA_MS gets
silence, not a forced comment. Calls stay short (roughly two sentences);
only the debrief runs longer.

When a reference is available (the Time Trial rival ghost, or the track
target table), deltas are spoken against it — "two tenths off the rival"
— rather than against the driver's own best. The WHY (corner causes)
always comes from the driver's own laps; see reference.py.

This module owns tone and phrasing only; the numbers come from
analysis.py, corner findings from trace.py, live context from state.py.
"""
import random

import config
import trace
from analysis import LapResult, SectorResult

SECTOR_ORDINALS = {1: "first", 2: "second", 3: "final"}
BIG_MOMENT_MS = 5000     # loss so large it's an off, not a delta worth reciting
STRONG_BEST_MS = 300     # a best by this much earns explicit praise
MIN_TEL_SAMPLES = 40     # don't comment on driving stats from a handful of samples
REF_MAX_TALK_MS = 10_000  # a reference gap beyond this isn't actionable — skip it


_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five",
                6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _delta_phrase(delta_ms: int) -> str:
    """Natural spoken magnitude: 'two hundredths', 'a tenth', 'three
    tenths', 'half a second', '1.2 seconds'."""
    ms = abs(delta_ms)
    if ms < 95:
        n = max(1, round(ms / 10))
        if n >= 10:
            return "a tenth"
        return "a hundredth" if n == 1 else f"{_COUNT_WORDS[n]} hundredths"
    if ms < 950:
        n = round(ms / 100)
        if n >= 10:
            return "a second"
        if n == 1:
            return "a tenth"
        if n == 5:
            return "half a second"
        return f"{_COUNT_WORDS[n]} tenths"
    secs = ms / 1000
    if abs(secs - round(secs)) < 0.05:
        n = round(secs)
        return "a second" if n == 1 else f"{n} seconds"
    return f"{secs:.1f} seconds"


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
    # Imperative coaching per cause — used for the dashboard advice line
    # and the debrief recommendation.
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

    # ---- flag calls: fixed, instant, safety radio stays short ----
    FLAG_CALLS = {
        "yellow": "Yellow flag ahead — take care.",
        "clear": "Track's clear again, green flag.",
        "red": "Red flag, red flag — session's stopped.",
        "sc": "Safety car's out.",
        "vsc": "Virtual safety car — hold your delta.",
        "sc_in": "Safety car's coming in this lap.",
        "green": "Green flag — race on.",
        "blue": "Blue flag — car behind is on a fast lap, let him through.",
        "chequered": "That's the chequered flag.",
        "drive_through": "That's a drive-through penalty for you.",
        "stop_go": "That's a stop-and-go penalty for you.",
    }

    def flag_line(self, kind: str, seconds: int | None = None) -> str | None:
        if kind == "time_penalty" and seconds:
            return f"You've picked up a {seconds} second time penalty."
        return self.FLAG_CALLS.get(kind)

    # ---- radio calls ----
    def radio_check(self) -> str:
        return self._pick("radio", [
            "Radio check — I can see the car. Telemetry's coming through.",
            "Radio check. I've got your data, we're live.",
        ])

    def new_session(self) -> str:
        return self._pick("new_session", [
            "New session — I've reset all the times.",
            "Fresh session, starting the times over.",
        ])

    # ---- corner detail: what happened and what to do about it ----
    def _corner_words(self, finding, track_id) -> str:
        name = trace.turn_name(track_id, finding.onset_m)
        you = "It looks like you" if finding.confidence != "high" else "You"
        if finding.cause == "overslowed":
            k = abs(finding.min_speed_diff)
            return (f"{you} overslowed {name} — about {k} kph too slow at "
                    f"the apex. Trust the grip and carry more speed through there.")
        if finding.cause == "braked_early":
            metres = round(abs(finding.onset_diff_m) / 5) * 5
            return (f"{you} braked about {metres} metres too early for "
                    f"{name}. Try going deeper on the brakes next time.")
        return (f"{you} were slow back on the power out of {name}. "
                f"Get on the throttle earlier there.")

    SHORT_ADVICE = {"braked_early": "brake later there",
                    "overslowed": "carry more speed through there",
                    "slow_exit": "get on the throttle earlier"}

    def live_corner_line(self, finding, track_id) -> str:
        """Immediate call as the car exits a corner it just lost time in.
        Short — the driver is mid-lap."""
        name = trace.turn_name(track_id, finding.onset_m)
        if finding.cause == "braked_early":
            metres = round(abs(finding.onset_diff_m) / 5) * 5
            return self._pick("live_early", [
                f"You braked early there at {name} — you can go about "
                f"{metres} metres deeper next time.",
                f"That was an early brake into {name}. Carry it about "
                f"{metres} metres further in.",
            ])
        if finding.cause == "overslowed":
            k = abs(finding.min_speed_diff)
            return self._pick("live_overslow", [
                f"You lost time at {name} just there — about {k} kph too "
                f"slow at the apex. Carry more speed next lap.",
                f"Bit cautious through {name} — {k} kph down. Trust it "
                f"more next time round.",
            ])
        return self._pick("live_exit", [
            f"You were slow on the throttle out of {name} — get on the "
            f"power sooner.",
            f"You lost the drive off {name}. Earlier on the throttle "
            f"next time.",
        ])

    def _memory_words(self, finding, verdict, track_id) -> str:
        """Repeat-mistake phrasing — the engineer remembers."""
        name = trace.turn_name(track_id, finding.onset_m)
        if verdict.kind == "repeat":
            return f"That's the same mistake again at {name}."
        fix = self.SHORT_ADVICE.get(finding.cause, "tidy it up")
        streak = _COUNT_WORDS.get(verdict.streak, verdict.streak)
        return f"That's {streak} laps in a row now at {name} — {fix}."

    def _driving_note(self, r: SectorResult) -> str | None:
        stats, best = r.stats, r.best_stats
        if (stats is None or best is None
                or stats.samples < MIN_TEL_SAMPLES or best.samples < MIN_TEL_SAMPLES):
            return None
        speed_diff = stats.top_speed - best.top_speed
        if speed_diff <= -5:
            return f"You're down about {abs(speed_diff)} kph on top speed."
        if stats.brake_count > best.brake_count:
            return "You had an extra dab of brakes in there."
        if stats.full_throttle_pct - best.full_throttle_pct <= -5:
            return "You spent less time at full throttle."
        return None

    # ---- sectors 1 and 2 (sector 3 is folded into the lap call) ----
    def sector_line(self, r: SectorResult, corner=None, track_id=None,
                    verdict=None, ref_delta_ms=None, ref_label=None) -> str | None:
        ordinal = SECTOR_ORDINALS[r.sector]
        if self.race_mode:
            return self._race_sector_line(r, ordinal, corner, track_id)
        threshold = config.CALLOUT_MIN_DELTA_MS * (2 if self.quali_mode else 1)
        # A reference gap beyond a big moment isn't coaching, it's noise —
        # fall back to self-comparison for this call.
        use_ref = (ref_delta_ms is not None and ref_label
                   and ref_delta_ms < BIG_MOMENT_MS)
        fixed = (f"Much better at "
                 f"{trace.turn_name(track_id, verdict.fixed_onset)} that time."
                 if verdict is not None and verdict.fixed_onset is not None
                 else None)

        # An off is always relative to your own pace.
        if r.delta_ms is not None and r.delta_ms >= BIG_MOMENT_MS:
            return self._pick("off", [
                f"Big moment in the {ordinal} sector — that lap's gone. "
                f"Reset and go again.",
                f"That was an off in the {ordinal} sector. Lap's done — "
                f"focus on the next one.",
            ])

        if r.is_best and r.delta_ms is not None:
            lead = f"That's your best {ordinal} sector so far"
            if abs(r.delta_ms) >= config.CALLOUT_MIN_DELTA_MS:
                lead += f" — {_delta_phrase(r.delta_ms)} quicker"
            lead += "."
            if use_ref and abs(ref_delta_ms) >= threshold:
                side = ("still " + _delta_phrase(ref_delta_ms) + " behind "
                        if ref_delta_ms > 0
                        else _delta_phrase(ref_delta_ms) + " ahead of ")
                lead += f" You're {side}{ref_label} there."
            if fixed:
                return f"{lead} {fixed}"
            return lead

        delta = ref_delta_ms if use_ref else r.delta_ms
        vs = f" to {ref_label}" if use_ref else ""
        if delta is None:
            return fixed
        if delta >= threshold:
            loss = (f"You lost {_delta_phrase(delta)}{vs} in the "
                    f"{ordinal} sector.")
            if (corner is not None and verdict is not None
                    and verdict.kind in ("repeat", "streak")):
                return f"{loss} {self._memory_words(corner, verdict, track_id)}"
            detail = (self._corner_words(corner, track_id) if corner is not None
                      else self._driving_note(r))
            return f"{loss} {detail}" if detail else loss
        if use_ref and delta <= -threshold:
            return (f"Good {ordinal} sector — {_delta_phrase(delta)} "
                    f"quicker than {ref_label}.")
        return fixed  # on pace — only an earned "better there" gets air

    def _race_sector_line(self, r: SectorResult, ordinal, corner,
                          track_id) -> str | None:
        """Race reference is stint pace, not the session-best hotlap.
        Normal race variance gets silence; bests still get called."""
        if r.is_best and r.delta_ms is not None:
            return f"That's your best {ordinal} sector of the race."
        sd = r.stint_delta_ms
        if sd is None:
            return None
        if sd >= BIG_MOMENT_MS:
            return self._pick("off", [
                f"Big moment in the {ordinal} sector — keep it together.",
                f"That was a moment in the {ordinal} sector. Settle it back down.",
            ])
        if sd >= config.RACE_CALLOUT_MIN_DELTA_MS:
            line = (f"You're {_delta_phrase(sd)} off your stint pace in "
                    f"the {ordinal} sector.")
            if corner is not None:
                return f"{line} {self._corner_words(corner, track_id)}"
            return line
        return None

    def _gain_words(self, gains, track_id) -> str | None:
        """Why the lap was faster — the top contributors, stated plainly."""
        real = sorted((g for g in gains if g.gain),
                      key=lambda g: g.score(), reverse=True)[:2]
        if not real:
            return None
        bits = []
        for g in real:
            name = trace.turn_name(track_id, g.onset_m)
            if g.cause == "carried_speed":
                bits.append(f"carrying {g.min_speed_diff} kph more through {name}")
            elif g.cause == "later_brakes":
                bits.append(f"braking later into {name}")
            else:
                bits.append(f"getting on the power earlier out of {name}")
        return "You found that time by " + ", and ".join(bits) + "."

    # ---- lap completion ----
    def lap_line(self, s3: SectorResult, lap: LapResult, session=None,
                 gains=None, theoretical_ms=None, track_id=None,
                 ref_delta_ms=None, ref_label=None) -> str:
        if self.race_mode:
            return self._race_lap_line(lap, session)
        use_ref = (ref_delta_ms is not None and ref_label
                   and abs(ref_delta_ms) < REF_MAX_TALK_MS)

        s3_part = None
        if s3.delta_ms is not None:
            if s3.is_best:
                s3_part = "Your best final sector so far."
            elif s3.delta_ms >= BIG_MOMENT_MS:
                s3_part = "You had a moment in the last sector."
            elif s3.delta_ms >= config.CALLOUT_MIN_DELTA_MS:
                s3_part = (f"You lost {_delta_phrase(s3.delta_ms)} in the "
                           f"last sector.")
        # A plain s3 delta that matches the lap delta says nothing the lap
        # call won't — drop it rather than read the same number twice.
        if (s3_part and not s3.is_best and lap.delta_ms is not None
                and not lap.is_best and s3.delta_ms < BIG_MOMENT_MS
                and abs(s3.delta_ms - lap.delta_ms) < config.CALLOUT_MIN_DELTA_MS):
            s3_part = None

        tw = _lap_time_words(lap.lap_time_ms)
        twc = tw[0].upper() + tw[1:]
        if lap.invalid:
            verdict = (f"That was a {tw}, but the lap's been deleted — "
                       f"track limits.")
        elif lap.delta_ms is None:
            if use_ref and abs(ref_delta_ms) >= config.CALLOUT_MIN_DELTA_MS:
                side = ("off" if ref_delta_ms > 0 else "quicker than")
                verdict = (f"First lap on the board — a {tw}. That's "
                           f"{_delta_phrase(ref_delta_ms)} {side} "
                           f"{ref_label}'s pace.")
            else:
                verdict = (f"First lap on the board — a {tw}. "
                           f"I'll coach you off this one.")
        elif lap.is_best:
            explanation = self._gain_words(gains, track_id) if gains else None
            verdict = f"New best lap — a {tw}."
            if explanation:
                verdict += f" {explanation}"
                s3_part = None  # the explanation is the story
            if use_ref and abs(ref_delta_ms) >= config.CALLOUT_MIN_DELTA_MS:
                if ref_delta_ms > 0:
                    verdict += (f" Still {_delta_phrase(ref_delta_ms)} off "
                                f"{ref_label}.")
                else:
                    verdict += (f" That puts you {_delta_phrase(ref_delta_ms)} "
                                f"ahead of {ref_label}.")
            elif (not explanation and theoretical_ms is not None
                    and theoretical_ms <= lap.lap_time_ms - STRONG_BEST_MS):
                verdict += (f" Put your best sectors together and there's a "
                            f"{_lap_time_words(theoretical_ms)} in it.")
        elif use_ref and abs(ref_delta_ms) >= config.CALLOUT_MIN_DELTA_MS:
            if ref_delta_ms > 0:
                verdict = (f"That's a {tw} — {_delta_phrase(ref_delta_ms)} "
                           f"off {ref_label}'s pace.")
                if s3_part is None and lap.worst_sector:
                    verdict += (f" Most of it went in the "
                                f"{SECTOR_ORDINALS[lap.worst_sector]} sector.")
            else:
                verdict = (f"That's a {tw} — {_delta_phrase(ref_delta_ms)} "
                           f"quicker than {ref_label}. Keep doing that.")
        elif lap.delta_ms < config.CALLOUT_MIN_DELTA_MS:
            verdict = f"{twc} — level with your best."
        elif s3_part is None and lap.worst_sector:
            verdict = (f"That's a {tw} — {_delta_phrase(lap.delta_ms)} "
                       f"slower, most of it in the "
                       f"{SECTOR_ORDINALS[lap.worst_sector]} sector.")
        else:
            verdict = f"That's a {tw} — {_delta_phrase(lap.delta_ms)} off your best."

        if s3_part:
            return f"{s3_part} {verdict}"
        note = None if self.quali_mode else self._context_note(lap, session)
        if note and verdict.count(". ") == 0:  # keep calls short
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
            lead = f"That's the fastest lap of the race — a {tw}."
        elif pos:
            lead = f"P{pos}, {tw}."
        else:
            lead = f"{tw[0].upper() + tw[1:]}."

        sd = lap.stint_delta_ms
        if sd is not None and sd >= config.RACE_CALLOUT_MIN_DELTA_MS and not lap.is_best:
            second = (f"Your pace is dropping — {_delta_phrase(sd)} slower "
                      f"than your stint average.")
        elif pos and pos > 1 and gap_ms and 0 < gap_ms < 60_000:
            second = f"The gap to the car ahead is {_delta_phrase(gap_ms)}."
        else:
            second = self._context_note(lap, session)
        return f"{lead} {second}" if second else lead

    # ---- session debrief: deliberate summary, may run a few sentences ----
    def recap_words(self, r: dict | None, recurring=None, track_id=None,
                    prev_best_ms=None, weak_zones=None) -> str:
        if r is None or r["laps"] == 0:
            return "Nothing on the board yet — get a lap in and I'll break it down."
        parts = [f"Right — {r['laps']} laps on the board.",
                 f"Your best was a {_lap_time_words(r['best_lap_ms'])}."]
        remaining = r["best_lap_ms"] - r["theoretical_ms"]
        if remaining > config.CALLOUT_MIN_DELTA_MS:
            parts.append(f"Put your best sectors together and you'd do a "
                         f"{_lap_time_words(r['theoretical_ms'])} — there's "
                         f"{_delta_phrase(remaining)} still on the table.")
        gap = r["weakest_gap_ms"]
        if gap >= config.CALLOUT_MIN_DELTA_MS:
            parts.append(f"Most of your time is going in the "
                         f"{SECTOR_ORDINALS[r['weakest_sector']]} sector — "
                         f"about {_delta_phrase(gap)} every lap. Your "
                         f"strongest is the "
                         f"{SECTOR_ORDINALS[r['strongest_sector']]}.")
        else:
            parts.append("Your sectors are nice and even.")
        CAUSE_WORDS = {"braked_early": "braking too early",
                       "overslowed": "losing apex speed",
                       "slow_exit": "slow exits"}
        if weak_zones and len(weak_zones) >= 2:
            bits = [f"{trace.turn_name(track_id, onset_m)} — "
                    f"{CAUSE_WORDS.get(cause, cause)}"
                    for onset_m, cause, avg, laps in weak_zones[:3]]
            parts.append("Your biggest losses were at " + "; ".join(bits) + ".")
            top_onset, top_cause, top_avg, top_laps = weak_zones[0]
            fix = self.SHORT_ADVICE.get(top_cause, "tidy it up")
            unit = "metres" if top_cause == "braked_early" else "kph"
            parts.append(f"If you practise one thing, {fix} at "
                         f"{trace.turn_name(track_id, top_onset)} — that one "
                         f"cost you on {top_laps} laps, about {top_avg} {unit} "
                         f"each time.")
        elif recurring is not None:
            onset_m, cause, avg, laps = recurring
            name = trace.turn_name(track_id, onset_m)
            what = CAUSE_WORDS.get(cause, cause)
            unit = "metres" if cause == "braked_early" else "kph"
            fix = self.SHORT_ADVICE.get(cause, "tidy it up")
            parts.append(f"Your recurring one is {what} at {name} — it cost "
                         f"you on {laps} laps, about {avg} {unit} each time. "
                         f"If you practise one thing, {fix}.")
        improvement = r.get("improvement_ms")
        if improvement is not None and improvement >= 300:
            parts.append(f"Your pace came on by {_delta_phrase(improvement)} "
                         f"over the session.")
        sd = r["consistency_ms"]
        if sd is not None:
            parts.append("Your consistency's good." if sd < 300 else
                         f"Lap times are swinging by about "
                         f"{_delta_phrase(sd)} — worth tightening up.")
        if prev_best_ms is not None:
            diff = r["best_lap_ms"] - prev_best_ms
            if diff <= -config.CALLOUT_MIN_DELTA_MS:
                parts.append(f"And you're {_delta_phrase(diff)} quicker than "
                             f"your last session here. Good progress.")
            elif diff >= config.CALLOUT_MIN_DELTA_MS:
                parts.append(f"You're still {_delta_phrase(diff)} off your "
                             f"last session here.")
        return " ".join(parts)

    def track_history_line(self, best_ms: int) -> str:
        return (f"Last time you were here, your best was a "
                f"{_lap_time_words(best_ms)}.")

    def _context_note(self, lap: LapResult, session) -> str | None:
        """One factual status line, priority: trap speed, tyres, fuel."""
        if session is None:
            return None
        trap_kph = session.speed_trap_kph or 0
        if trap_kph and lap.lap_num >= 2 and trap_kph >= self._trap_announced + 3:
            self._trap_announced = trap_kph
            return f"Best top speed so far — {round(trap_kph)} kph."
        age = session.tyre_age_laps
        if age is not None and age >= self._next_tyre_note:
            self._next_tyre_note = age + 4
            return f"Those tyres are {age} laps old now."
        fuel = session.fuel_remaining_laps
        if (fuel is not None and self._fuel_thresholds
                and 0 < fuel < self._fuel_thresholds[0]):
            self._fuel_thresholds.pop(0)
            return f"You've got about {fuel:.1f} laps of fuel left."
        return None
