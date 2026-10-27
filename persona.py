"""The engineer as a person — delivery, habits and moods.

brain.py decides WHAT is said. This module decides how it comes out of a
human mouth: the "Okay," before a call, the driver's name now and then, the
lift in pace when he is pleased and the clipped speed of a safety call, and
the little things that make a voice feel like it is paying attention — it
greets you differently in the evening, it notices you have strung three
bests together, it asks if you are all right after an off.

The rules are those of a good engineer on the radio: never chatter for the
sake of it, never use the name every line (it turns into a tic), and never
put a filler in front of a safety call — those go out bare and fast.
"""
import random
import re
import time

# How each style is delivered. speed multiplies the base TTS speed.
STYLES = {
    "urgent": {"speed": 1.12},   # safety calls: clipped and quick
    "excited": {"speed": 1.10},  # a new best — he is pleased, it shows
    "warm": {"speed": 1.04},     # reassurance, encouragement
    "calm": {"speed": 0.98},     # the debrief: measured, unhurried
}

# The driver should hear his name about once every few minutes, not on
# every call — more than that and it becomes a verbal tic.
NAME_COOLDOWN_S = 150

OPENERS = ["Okay,", "Right,", "Alright,", "Okay so,"]
# Share of routine calls that get a spoken opener. Real engineers use one
# on maybe a third of their calls; more sounds scripted.
OPENER_RATE = 0.3


class Line(str):
    """A spoken line that carries how it should be delivered. It is still a
    str everywhere (logs, the dashboard transcript, tests); the voice reads
    the style off it, and Persona.deliver reads the kind — what sort of
    call it is decides whether it may carry an opener or the name."""
    style = None
    kind = None

    def __new__(cls, text: str, style: str | None = None, kind: str | None = None):
        obj = super().__new__(cls, text)
        obj.style = style
        obj.kind = kind
        return obj


def styled(text: str | None, style: str | None = None,
           kind: str | None = None) -> Line | None:
    if text is None:
        return None
    return Line(text, style, kind)


def speed_for(text, base: float) -> float:
    style = STYLES.get(getattr(text, "style", None) or "")
    return base * (style["speed"] if style else 1.0)


def part_of_day(hour: int | None = None) -> str:
    hour = time.localtime().tm_hour if hour is None else hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 18:
        return "afternoon"
    return "evening"


class Persona:
    def __init__(self, driver_name: str | None = None, rng: random.Random | None = None,
                 clock=time.monotonic):
        self.name = (driver_name or "").strip() or None
        self._rng = rng or random.Random()
        self._clock = clock
        self._last_name_use = -NAME_COOLDOWN_S
        self._last_opener = None
        self._opened_last = False  # the previous coaching call had an opener

    def deliver(self, text: str) -> Line:
        """The last touch before a line goes on air. What a line may carry
        depends on its kind:

          greet / recap / finish — always worth the driver's name
          best / off / answer    — the name when it is due
          coaching (no kind)     — sometimes an "Okay," in front
          flag / info            — bare: facts and safety go out as-is
        """
        style = getattr(text, "style", None)
        kind = getattr(text, "kind", None)
        out = str(text)
        if kind in ("greet", "recap", "finish"):
            out = self.address(out, force=True)
        elif kind in ("best", "off", "answer"):
            out = self.address(out)
        elif kind is None:
            out = self.open(out)
        return Line(out, style, kind)

    # ---- the driver's name ----
    def name_ok(self) -> bool:
        return (self.name is not None
                and self._clock() - self._last_name_use >= NAME_COOLDOWN_S)

    def address(self, text: str, force: bool = False) -> str:
        """Work the driver's name in, when it is due. After a short opening
        clause it sits where a person would put it ("Good lap, Jordan. New
        best..."); otherwise it leads ("Jordan, that's a one 28...")."""
        if self.name is None or not (force or self.name_ok()):
            return text
        self._last_name_use = self._clock()
        m = _FIRST_CLAUSE.match(text)
        if m and len(m.group(1).split()) <= 4:
            head, punct, rest = m.groups()
            return f"{head}, {self.name}{punct} {rest}".rstrip()
        return f"{self.name}, {_lower_first(text)}"

    # ---- the little words in front of a call ----
    def open(self, text: str, rate: float = OPENER_RATE) -> str:
        """Sometimes put an "Okay," in front — never on two calls running,
        never on a line that already opens with one."""
        if self._opened_last or self._rng.random() >= rate or _has_opener(text):
            self._opened_last = False
            return text
        self._opened_last = True
        choices = [o for o in OPENERS if o != self._last_opener]
        opener = self._rng.choice(choices)
        self._last_opener = opener
        return f"{opener} {_lower_first(text)}"


# "Good lap." / "New best lap —" / "Okay," — a short clause the name can
# follow. The em-dash keeps its leading space.
_FIRST_CLAUSE = re.compile(r"^([^.,!?\u2014]{1,40}?)([.,!?]| \u2014)\s*(.*)$")


def _has_opener(text: str) -> bool:
    head = text.split(" ", 1)[0].rstrip(",").lower()
    return head in {"okay", "right", "alright", "copy", "good", "so"}


def _lower_first(text: str) -> str:
    """Lower-case the first word so the line can follow an opener — unless
    it is "I", an acronym, or a token like "P3"."""
    head = text.split(" ", 1)[0]
    keep = (head in {"I", "I'm", "I'll", "I've"}
            or any(c.isdigit() for c in head)
            or (len(head) > 1 and head[1:2].isupper()))
    return text if keep or not text else text[0].lower() + text[1:]
