#!/usr/bin/env python3
"""Render a short demo of the engineer on team radio to a WAV file.

    build-venv/bin/python scripts/radio_demo.py out.wav [--name Jordan]
        [--voice bm_lewis] [--clean]

A handful of real calls — radio check, a corner note, a new best, a safety
car, an answer to a question, the flag — through exactly the chain the app
uses (brain → persona → kokoro → radio). With --compare, the first call is
also rendered without the radio sound, so the two can be heard side by side.
"""
import argparse
import random
import sys
import threading
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import brain  # noqa: E402
import config  # noqa: E402
import persona  # noqa: E402
import speak  # noqa: E402
from analysis import LapResult, SectorResult  # noqa: E402


class _Corner:
    """An early-braking finding at Silverstone's Village (turn three)."""
    cause = "braked_early"
    onset_m = 640.0
    onset_diff_m = -28
    confidence = "high"
    min_speed_diff = 0


def demo_lines(name: str | None):
    e = brain.RaceEngineer()
    e._rng.seed(3)
    p = persona.Persona(name, random.Random(4))

    def sec(sector, delta, best=False):
        return SectorResult(3, sector, 31_000, delta, best, False)

    def lap(n, ms, delta, best):
        return LapResult(n, ms, delta, best, False, None)

    raw = [
        e.radio_check(hour=21),
        e.sector_line(sec(1, 320), _Corner(), 7),
        e.lap_line(sec(3, -80, True), lap(3, 88_421, -310, True)),
        e.flag_line("sc"),
        e.answer("how are my tyres", {"compound": "SOFT", "tyre_age": 9,
                                      "wear_corners": [24, 26, 38, 33]}),
        e.lap_line(sec(3, -60, True), lap(4, 88_102, -319, True)),
        e.finish_line(2, True),
    ]
    return [p.deliver(line) for line in raw]


def render(tts, line) -> tuple[np.ndarray, int]:
    speed = persona.speed_for(line, tts.speed)
    chunks = speak._chunks(line)
    parts, sr = [], 24_000
    for i, chunk in enumerate(chunks):
        path = tts._synth_wav(chunk, speed, i == 0, i == len(chunks) - 1)
        with wave.open(path) as w:
            sr = w.getframerate()
            parts.append(np.frombuffer(w.readframes(w.getnframes()), np.int16))
        speak._unlink(path)
    return np.concatenate(parts), sr


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out")
    ap.add_argument("--name", default=config.DRIVER_NAME or None)
    ap.add_argument("--voice", default=config.KOKORO_VOICE)
    ap.add_argument("--clean", action="store_true", help="no radio sound")
    ap.add_argument("--compare", action="store_true",
                    help="open with the first call without, then with, radio")
    args = ap.parse_args()

    tts = speak.KokoroTTS.__new__(speak.KokoroTTS)  # no worker threads
    tts.voice, tts.speed, tts.radio_fx = args.voice, config.KOKORO_SPEED, not args.clean
    tts._kokoro, tts._precache, tts._cache = None, (), {}
    tts._cache_lock, tts._synth_lock = threading.Lock(), threading.Lock()

    lines = demo_lines(args.name)
    reel, sr = [], 24_000
    if args.compare:
        tts.radio_fx = False
        audio, sr = render(tts, lines[0])
        reel += [audio, np.zeros(int(sr * 1.2), np.int16)]
        tts.radio_fx = not args.clean
    for line in lines:
        print(f"  {line}")
        audio, sr = render(tts, line)
        reel += [audio, np.zeros(int(sr * 0.9), np.int16)]
    with wave.open(args.out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.concatenate(reel).tobytes())
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
