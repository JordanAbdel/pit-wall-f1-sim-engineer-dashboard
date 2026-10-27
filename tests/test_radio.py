"""The team-radio sound (radio.py) and the engineer's delivery (persona.py).

The radio chain is judged on what makes it radio — the energy sits in the
voice band, the press and release are audible, quiet syllables come up —
and on what must not break: it never clips, never goes silent, and a
streamed line is one transmission, not a click per sentence."""
import random

import numpy as np
import pytest

import persona
import radio

SR = 24_000


def voice_like(seconds=1.5, seed=0):
    """A rough stand-in for speech: harmonics of a wandering pitch, with
    syllable-rate loudness swings and some broadband breath."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(SR * seconds)) / SR
    f0 = 120 + 20 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / SR
    x = sum(np.sin(k * phase) / k for k in range(1, 30))
    x *= 0.2 + 0.8 * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)) ** 2
    x += 0.05 * rng.standard_normal(len(t))
    return (x / np.max(np.abs(x)) * 0.5).astype(np.float32)


def band_share(y, lo, hi):
    f = np.fft.rfftfreq(len(y), 1 / SR)
    p = np.abs(np.fft.rfft(y)) ** 2
    return p[(f >= lo) & (f < hi)].sum() / p.sum()


def test_energy_lives_in_the_radio_band():
    y = radio.transmission(voice_like(), SR, np.random.default_rng(1))
    assert band_share(y, 250, 3800) > 0.9
    assert band_share(y, 0, 200) < 0.02      # no chest — it's a radio
    assert band_share(y, 5000, 12000) < 0.01  # no air


def test_never_clips_and_never_goes_silent():
    y = radio.transmission(voice_like(), SR, np.random.default_rng(2))
    assert y.dtype == np.float32
    assert np.max(np.abs(y)) <= 1.0
    assert np.sqrt(np.mean(y ** 2)) > 0.05


def test_compression_brings_quiet_syllables_up():
    x = voice_like()
    x[: len(x) // 2] *= 0.1   # first half whispered
    x = x / np.max(np.abs(x))  # as transmission() hands it over
    y = radio.compress(radio.band_limit(x.astype(np.float64), SR), SR)
    half = len(y) // 2
    quiet = np.sqrt(np.mean(y[:half] ** 2))
    loud = np.sqrt(np.mean(y[half:] ** 2))
    # 20 dB apart going in; much closer coming out.
    assert loud / quiet < 4


def test_press_and_release_add_carrier_and_tail():
    x = voice_like(0.5)
    whole = radio.transmission(x, SR, np.random.default_rng(3))
    middle = radio.transmission(x, SR, np.random.default_rng(3),
                                key_up=False, key_down=False)
    # The release tail and pre-roll only exist on a full transmission.
    assert len(whole) - len(middle) > SR * 0.15
    # ... and the static burst is audible at the very end.
    tail = whole[-int(SR * 0.11):-int(SR * 0.07)]
    assert np.sqrt(np.mean(tail ** 2)) > 0.02


def test_empty_and_silent_input_are_harmless():
    assert len(radio.transmission(np.zeros(0), SR)) == 0
    y = radio.transmission(np.zeros(2400), SR)  # a stub TTS take
    assert np.all(np.isfinite(y)) and np.max(np.abs(y)) <= 1.0


def test_every_take_is_a_little_different():
    x = voice_like(0.4)
    a = radio.transmission(x, SR, np.random.default_rng(4))
    b = radio.transmission(x, SR, np.random.default_rng(5))
    assert len(a) == len(b) and not np.allclose(a, b)


# ---- persona ----

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_name_is_rationed():
    clock = Clock()
    p = persona.Persona("Jordan", random.Random(0), clock)
    first = p.deliver(persona.Line("Good lap. New best lap — a one 28 point 4.",
                                   "excited", "best"))
    assert first.startswith("Good lap, Jordan.")
    second = p.deliver(persona.Line("New best lap — a one 28 point 1.",
                                    "excited", "best"))
    assert "Jordan" not in second        # too soon
    clock.t += persona.NAME_COOLDOWN_S
    third = p.deliver(persona.Line("New best lap — a one 28 point 0.",
                                   "excited", "best"))
    assert third.startswith("New best lap, Jordan —")


def test_greetings_always_use_the_name():
    p = persona.Persona("Jordan", random.Random(0), Clock())
    p.address("Hello.")  # spends the cooldown
    line = p.deliver(persona.Line("Good evening. Radio check — I can see the car.",
                                  "warm", "greet"))
    assert line.startswith("Good evening, Jordan.")


def test_long_first_clause_puts_the_name_first():
    p = persona.Persona("Jordan", random.Random(0), Clock())
    out = p.address("That's a one twenty-eight point four — two tenths off.")
    assert out.startswith("Jordan, that's a one")


def test_no_name_configured_changes_nothing():
    p = persona.Persona("", random.Random(0), Clock())
    line = persona.Line("Good lap. New best.", "excited", "best")
    assert str(p.deliver(line)) == "Good lap. New best."


def test_flags_go_out_bare():
    p = persona.Persona("Jordan", random.Random(0), Clock())
    for _ in range(20):
        out = p.deliver(persona.Line("Safety car's out.", "urgent", "flag"))
        assert str(out) == "Safety car's out."
        assert out.style == "urgent"


def test_openers_are_occasional_and_never_back_to_back():
    p = persona.Persona(None, random.Random(7), Clock())
    outs = [str(p.deliver("You lost two tenths in the first sector."))
            for _ in range(200)]
    opened = [not o.startswith("You") for o in outs]
    assert 0.1 < sum(opened) / len(opened) < 0.4
    assert not any(a and b for a, b in zip(opened, opened[1:]))
    assert all(o.endswith("you lost two tenths in the first sector.")
               for o, op in zip(outs, opened) if op)


def test_openers_leave_tokens_like_p3_alone():
    p = persona.Persona(None, random.Random(0), Clock())
    out = p.open("P3, one 28.", rate=1)
    assert out != "P3, one 28." and out.endswith(", P3, one 28.")


def test_style_sets_speed():
    assert persona.speed_for(persona.Line("x", "urgent"), 1.0) > 1.0
    assert persona.speed_for(persona.Line("x", "calm"), 1.0) < 1.0
    assert persona.speed_for("plain", 1.08) == pytest.approx(1.08)


def test_part_of_day():
    assert persona.part_of_day(8) == "morning"
    assert persona.part_of_day(14) == "afternoon"
    assert persona.part_of_day(21) == "evening"
    assert persona.part_of_day(2) == "evening"
