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
import persona
import trace
from analysis import LapResult, SectorResult
from persona import styled

SECTOR_ORDINALS = {1: "first", 2: "second", 3: "final"}
BIG_MOMENT_MS = 5000     # loss so large it's an off, not a delta worth reciting
STRONG_BEST_MS = 300     # a best by this much earns explicit praise
MIN_TEL_SAMPLES = 40     # don't comment on driving stats from a handful of samples
REF_MAX_TALK_MS = 10_000  # a reference gap beyond this isn't actionable — skip it


_COUNT_WORDS = {2: "two", 3: "three", 4: "four", 5: "five",
                6: "six", 7: "seven", 8: "eight", 9: "nine"}

# Tyre order in every F1 UDP format: rear left, rear right, front left,
# front right.
CORNER_NAMES = ("rear left", "rear right", "front left", "front right")
TYRE_WEAR_CALLS = (50, 70)   # % worn on the worst corner — call each once
TREND_MIN_MS = 200           # quicker than the previous lap by this = say so

# Game weather codes: 0 clear, 1 light cloud, 2 overcast, 3 light rain,
# 4 heavy rain, 5 storm.
WEATHER_WORDS = {0: "clear", 1: "light cloud", 2: "overcast",
                 3: "light rain", 4: "heavy rain", 5: "a storm"}


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
        self.total_laps = None   # race distance, from the Session packet
        self._best_streak = 0    # consecutive laps that were each a new best
        self._prev_lap_ms = None  # the lap before this one, for trend calls
        self._wear_called = set()

    def _pick(self, key: str, options: list) -> str:
        """Never repeat the previous phrasing for the same situation."""
        last = self._last_pick.get(key)
        choices = [o for o in options if o != last] or options
        choice = self._rng.choice(choices)
        self._last_pick[key] = choice
        return choice

    # ---- flag calls: fixed, instant, safety radio stays short ----
    # The big ones are said twice, the way engineers do: a call clipped by
    # a radio drop-out or engine noise still gets through on the repeat.
    FLAG_CALLS = {
        "yellow": "Yellow flag, yellow flag ahead — take care.",
        "clear": "Track's clear again, green flag.",
        "red": "Red flag, red flag — session's stopped.",
        "sc": "Safety car, safety car. The safety car is out.",
        "vsc": "Virtual safety car, virtual safety car — hold your delta.",
        "sc_in": "Safety car's coming in this lap.",
        "green": "Green, green, green — race on.",
        "blue": "Blue flag — car behind is on a fast lap, let him through.",
        "chequered": "That's the chequered flag.",
        "drive_through": "That's a drive-through penalty for you.",
        "stop_go": "That's a stop-and-go penalty for you.",
    }

    def flag_line(self, kind: str, seconds: int | None = None) -> str | None:
        if kind == "time_penalty" and seconds:
            return styled(f"You've picked up a {seconds} second time penalty.",
                          "urgent", "flag")
        return styled(self.FLAG_CALLS.get(kind), "urgent", "flag")

    # ---- radio calls ----
    def radio_check(self, hour: int | None = None) -> str:
        when = persona.part_of_day(hour)
        return styled(self._pick("radio", [
            f"Good {when}. Radio check — I can see the car, telemetry's "
            f"coming through.",
            f"Radio check. Good {when} — I've got your data, we're live.",
            f"Good {when}. Radio check — loud and clear this end, and I've "
            f"got the car on my screens. Let's get to work.",
        ]), "warm", "greet")

    def new_session(self) -> str:
        return styled(self._pick("new_session", [
            "New session — I've reset all the times.",
            "Fresh session, starting the times over.",
            "Okay, new session. Clean sheet — times are reset.",
        ]), None, "info")

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
            return styled(self._pick("off", [
                f"Big moment in the {ordinal} sector — that lap's gone. "
                f"Reset and go again.",
                f"That was an off in the {ordinal} sector. Lap's done — "
                f"focus on the next one.",
                f"You okay? That was a big one in the {ordinal} sector. "
                f"Lap's gone — take a breath and go again.",
            ]), "warm", "off")

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
        """The call as the car crosses the line. Remembers the lap before,
        so a streak of bests or a lap that bounced back gets noticed."""
        prev_ms, self._prev_lap_ms = self._prev_lap_ms, (
            None if lap.invalid else lap.lap_time_ms)
        if lap.is_best and lap.delta_ms is not None and not lap.invalid:
            self._best_streak += 1
        else:
            self._best_streak = 0
        if self.race_mode:
            line = self._race_lap_line(lap, session)
            if (self.total_laps and lap.lap_num + 1 == self.total_laps):
                line += " " + self._pick("last_lap", [
                    "Last lap now — bring it home.",
                    "Final lap. Nice and clean, bring it home.",
                ])
            return styled(line, "excited" if self._best_streak else None,
                          "best" if self._best_streak else None)
        line = self._hotlap_line(s3, lap, session, gains, theoretical_ms,
                                 track_id, ref_delta_ms, ref_label)
        if self._best_streak:
            return styled(line, "excited", "best")
        if (prev_ms is not None and not lap.invalid and lap.delta_ms is not None
                and prev_ms - lap.lap_time_ms >= TREND_MIN_MS
                and len(line) < 60):  # keep calls short
            line += " " + self._pick("trend", [
                "Better than last time round, though.",
                "That's coming back — quicker than the last one.",
                f"{_delta_phrase(prev_ms - lap.lap_time_ms).capitalize()} "
                f"up on the lap before.",
            ])
        return line

    def _hotlap_line(self, s3: SectorResult, lap: LapResult, session,
                     gains, theoretical_ms, track_id,
                     ref_delta_ms, ref_label) -> str:
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
            streak = self._best_streak
            if streak >= 3:
                verdict = self._pick("best3", [
                    f"New best lap — a {tw}. That's {_COUNT_WORDS.get(streak, streak)} "
                    f"in a row, you're flying.",
                    f"New best lap again — a {tw}. {_COUNT_WORDS.get(streak, streak).capitalize()} "
                    f"on the bounce. Mega.",
                ])
            elif streak == 2:
                verdict = self._pick("best2", [
                    f"New best lap — a {tw}. Another step.",
                    f"New best lap — a {tw}. Back-to-back bests, keep it coming.",
                ])
            else:
                verdict = self._pick("best1", [
                    f"New best lap — a {tw}.",
                    f"Good lap. New best lap — a {tw}.",
                    f"New best lap — a {tw}. Nice.",
                ])
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
            verdict = self._pick("level", [
                f"{twc} — level with your best.",
                f"{twc}. Right on your best — nice and consistent.",
            ])
        elif s3_part is None and lap.worst_sector:
            verdict = (f"That's a {tw} — {_delta_phrase(lap.delta_ms)} "
                       f"slower, most of it in the "
                       f"{SECTOR_ORDINALS[lap.worst_sector]} sector.")
        else:
            gap = _delta_phrase(lap.delta_ms)
            verdict = self._pick("off_best", [
                f"That's a {tw} — {gap} off your best.",
                f"{twc}. {gap.capitalize()} off your best.",
            ])

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
            return styled("Nothing on the board yet — get a lap in and I'll "
                          "break it down.", "calm", "answer")
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
        return styled(" ".join(parts), "calm", "recap")

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

    # ---- things a real engineer volunteers ----
    def tyre_wear_line(self, wear_corners) -> str | None:
        """Once per threshold per set of tyres: the worst corner by name."""
        if not wear_corners or len(wear_corners) != 4:
            return None
        worst = max(range(4), key=lambda i: wear_corners[i])
        pct = wear_corners[worst]
        due = [t for t in TYRE_WEAR_CALLS if pct >= t and t not in self._wear_called]
        if not due:
            return None
        self._wear_called.update(due)
        corner = CORNER_NAMES[worst]
        if max(due) >= TYRE_WEAR_CALLS[-1]:
            return styled(self._pick("wear_high", [
                f"The {corner} is at {round(pct)} percent wear now. Look after "
                f"it — it's going to start falling away.",
                f"{corner.capitalize()}'s {round(pct)} percent worn. Manage it "
                f"through the long corners.",
            ]), None, "info")
        return styled(self._pick("wear_mid", [
            f"Tyres are past half-way — the {corner} is at {round(pct)} percent.",
            f"Heads up, {corner} is the one going first. {round(pct)} percent.",
        ]), None, "info")

    def new_tyres_line(self, compound: str | None) -> str:
        """Wear dropped back to nothing: a fresh set went on."""
        self._wear_called.clear()
        what = f"{compound.lower()}s" if compound else "tyres"
        return styled(self._pick("new_tyres", [
            f"Fresh {what} on. Bring them in gently, then let's go.",
            f"New {what}. Give them a lap to come in.",
        ]), "warm", None)

    def weather_line(self, old: int, new: int) -> str | None:
        """Weather moved between dry and wet, or the rain got heavier."""
        wet_old, wet_new = old >= 3, new >= 3
        word = WEATHER_WORDS.get(new)
        if word is None:
            return None
        if wet_new and not wet_old:
            return styled(f"Rain's arrived — {word}. Careful on the kerbs and "
                          f"the white lines.", None, "info")
        if wet_old and not wet_new:
            return styled("Rain's stopped. A dry line will start to come.",
                          None, "info")
        if wet_new and new > old:
            return styled(f"It's getting heavier — {word} now. Take it easy.",
                          None, "info")
        return None

    def finish_line(self, position: int | None, race: bool) -> str:
        """After the chequered flag — the moment the engineer sounds human."""
        if race and position == 1:
            return styled(self._pick("win", [
                "P1! Get in there! What a job. Bring it home.",
                "That's the win! P1. Brilliant drive.",
            ]), "excited", "finish")
        if race and position and position <= 3:
            return styled(f"P{position} — that's a podium. Good job.",
                          "excited", "finish")
        if race and position:
            return styled(self._pick("finish", [
                f"P{position} at the flag. Good work today.",
                f"That's P{position}. Solid race — cool-down lap now.",
            ]), "warm", "finish")
        return styled("Good session. Bring it back in.", "warm", "finish")

    # ---- the driver talks back ----
    INTENTS = (
        ("radio", ("radio check", "hear me", "can you hear", "testing")),
        ("thanks", ("thanks", "thank you", "cheers", "nice one")),
        # Before "position": "where am I losing time" is not "where am I".
        ("where", ("losing time", "losing the time", "doing wrong", "improve",
                   "where's the time", "where is the time", "weak", "slow",
                   "what should i work on", "faster")),
        ("gap", ("gap", "ahead", "in front", "car in front", "behind")),
        ("position", ("position", "where am i", "what position", "p what")),
        ("tyres", ("tyre", "tire", "wear", "rubber", "grip")),
        ("fuel", ("fuel", "petrol", "lift and coast")),
        ("laps_left", ("laps left", "how many laps", "laps to go", "remaining")),
        ("weather", ("weather", "rain", "wet", "dry", "temperature", "temp")),
        ("pace", ("pace", "how am i doing", "lap time", "best lap", "my best",
                  "last lap", "how was that", "delta")),
    )

    def intent(self, question: str) -> str | None:
        q = " " + " ".join(question.lower().replace("?", " ").split()) + " "
        for name, keys in self.INTENTS:
            if any(k in q for k in keys):
                return name
        return None

    def answer(self, question: str, ctx: dict) -> str:
        """Reply to the driver over the radio — from the data, never made
        up. ctx is a snapshot the engineer assembles (see main.py)."""
        what = self.intent(question)
        if what is None:
            return styled(self._pick("unclear", [
                "Say again? I didn't catch that.",
                "Sorry, say again — you broke up a bit.",
            ]), None, "answer")
        return styled(getattr(self, f"_answer_{what}")(ctx), None, "answer")

    def _answer_radio(self, ctx) -> str:
        return self._pick("ans_radio", ["Loud and clear.", "Yep, got you loud and clear.",
                                        "Reading you five by five."])

    def _answer_thanks(self, ctx) -> str:
        return self._pick("ans_thanks", ["No worries.", "Anytime. Keep it going.",
                                         "Copy."])

    def _answer_gap(self, ctx) -> str:
        gap, pos = ctx.get("gap_ahead_ms"), ctx.get("position")
        if not ctx.get("race"):
            return "No one to race in this session — it's just you and the clock."
        if pos == 1:
            return "You're leading — nobody ahead of you."
        if gap:
            return f"The gap to the car ahead is {_delta_phrase(gap)}."
        return "I haven't got a gap to the car ahead yet."

    def _answer_position(self, ctx) -> str:
        pos = ctx.get("position")
        if not pos:
            return "I haven't got a position for you yet."
        total = ctx.get("total_laps")
        lap = ctx.get("lap")
        if ctx.get("race") and total and lap:
            return f"You're P{pos}, lap {lap} of {total}."
        return f"You're P{pos}."

    def _answer_tyres(self, ctx) -> str:
        wear, age, comp = ctx.get("wear_corners"), ctx.get("tyre_age"), ctx.get("compound")
        bits = []
        if comp:
            what = f"{comp.lower()}s"
            bits.append(f"You're on {what}" + (f", {age} laps old" if age else "") + ".")
        if wear and len(wear) == 4:
            worst = max(range(4), key=lambda i: wear[i])
            avg = sum(wear) / 4
            if wear[worst] < 15:
                bits.append("They're in good shape — hardly any wear.")
            else:
                bits.append(f"Average wear's {round(avg)} percent, the "
                            f"{CORNER_NAMES[worst]} is worst at {round(wear[worst])}.")
        return " ".join(bits) or "I'm not getting tyre data yet."

    def _answer_fuel(self, ctx) -> str:
        laps = ctx.get("fuel_laps")
        if laps is None:
            return "I'm not getting fuel data yet."
        if laps >= 0.5:
            return f"Fuel's fine — you're about {laps:.1f} laps to the good."
        if laps > -0.5:
            return "Fuel's right on target. No need to save."
        return (f"We're about {abs(laps):.1f} laps short on fuel. Lift and coast "
                f"into the big stops.")

    def _answer_laps_left(self, ctx) -> str:
        total, lap = ctx.get("total_laps"), ctx.get("lap")
        if not (ctx.get("race") and total and lap):
            return "No race distance in this session — run as long as you like."
        left = total - lap
        if left <= 0:
            return "This is the last lap."
        return f"{left} laps to go after this one." if left > 1 else "One more after this."

    def _answer_weather(self, ctx) -> str:
        word = WEATHER_WORDS.get(ctx.get("weather"))
        tt, at = ctx.get("track_temp"), ctx.get("air_temp")
        if word is None:
            return "I haven't got the weather yet."
        temps = (f" Track's {tt} degrees, air {at}." if tt is not None and at is not None
                 else "")
        return f"It's {word}.{temps}"

    def _answer_where(self, ctx) -> str:
        top = ctx.get("top_issue")
        if top:
            onset_m, cause, avg, laps = top
            name = trace.turn_name(ctx.get("track_id"), onset_m)
            fix = self.SHORT_ADVICE.get(cause, "tidy it up")
            how = {"braked_early": f", braking about {avg} metres early",
                   "overslowed": f", about {avg} kph down at the apex"}.get(cause, "")
            return (f"The big one is {name} — it's cost you on {laps} "
                    f"laps{how}. {fix.capitalize()}.")
        weak = ctx.get("weakest_sector")
        if weak and ctx.get("weakest_gap_ms", 0) >= config.CALLOUT_MIN_DELTA_MS:
            return (f"Most of it's in the {SECTOR_ORDINALS[weak]} sector — about "
                    f"{_delta_phrase(ctx['weakest_gap_ms'])} a lap. Nothing's "
                    f"repeating at one corner yet.")
        return "Nothing standing out yet. Give me a few more laps."

    def _answer_pace(self, ctx) -> str:
        best, last = ctx.get("best_ms"), ctx.get("last_ms")
        if not best:
            return "No laps on the board yet."
        out = f"Your best is a {_lap_time_words(best)}."
        if last and last != best:
            out += f" Last lap was {_delta_phrase(last - best)} off that."
        theo = ctx.get("theoretical_ms")
        if theo and best - theo >= config.CALLOUT_MIN_DELTA_MS:
            out += f" There's a {_lap_time_words(theo)} in it if you put it together."
        return out
