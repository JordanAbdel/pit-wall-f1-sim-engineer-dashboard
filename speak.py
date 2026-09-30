"""TTS output — swappable engine behind a tiny interface.

Engines:
  "kokoro" — neural TTS (kokoro-onnx), natural British voices. Model
             files are bundled in models/ — self-contained, no sibling
             project needed.
  "say"    — macOS built-in. Instant but robotic; also the automatic
             fallback if kokoro is unavailable or fails.

Speech runs on worker threads so a long utterance never blocks the packet
loop. There are two lanes:

  routine  — coaching. A small backlog; if lines back up, the oldest is
             dropped — stale coaching is worse than no coaching.
  priority — safety calls (flags, red flag, safety car, penalties), via
             say(text, priority=True). Never dropped by routine lines, and
             played by their own worker, so a call never waits behind a
             coaching line that is still being synthesized.

A safety call also makes every routine line in flight stale: the queued
ones are flushed, the one playing is cut off, and one still being
synthesized is thrown away rather than played after the call.
"""
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import wave
from collections import deque

import config

log = logging.getLogger("speak")

_IS_WIN = sys.platform.startswith("win")


class Announcer:
    def say(self, text: str, priority: bool = False) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class NullAnnouncer(Announcer):
    """Console-only mode — the main loop already prints every line."""

    def say(self, text: str, priority: bool = False) -> None:
        pass


PRIORITY, ROUTINE = "priority", "routine"


class _QueuedAnnouncer(Announcer):
    ROUTINE_BACKLOG = 2

    def __init__(self):
        self._init_lanes()
        self._workers = [threading.Thread(target=self._run, args=(lane,),
                                          name=f"tts-{lane}", daemon=True)
                         for lane in (PRIORITY, ROUTINE)]
        for w in self._workers:
            w.start()

    def _init_lanes(self) -> None:
        self._cv = threading.Condition()
        self._lanes = {PRIORITY: deque(), ROUTINE: deque()}
        self._procs = {PRIORITY: None, ROUTINE: None}  # playing subprocesses
        # True from the moment a safety call is queued until the last one
        # has finished playing; routine lines wait rather than talk over it.
        self._priority_active = False
        # Bumped by every safety call. A routine line remembers the value it
        # was taken under; if it has moved on, the line is stale.
        self._gen = 0
        self._closed = False
        self._local = threading.local()  # lane + generation of this worker

    def say(self, text: str, priority: bool = False) -> None:
        with self._cv:
            if priority:
                flushed = len(self._lanes[ROUTINE])
                self._lanes[ROUTINE].clear()
                if flushed:
                    log.info("safety call — flushed %d queued line(s)", flushed)
                self._lanes[PRIORITY].append(text)
                self._priority_active = True
                self._gen += 1
                proc = self._procs[ROUTINE]
                if proc is not None and proc.poll() is None:
                    proc.terminate()
            else:
                routine = self._lanes[ROUTINE]
                if len(routine) >= self.ROUTINE_BACKLOG:
                    log.warning("TTS backlog, dropped: %r", routine.popleft())
                routine.append(text)
            self._cv.notify_all()

    def _stale(self) -> bool:
        """True when the routine line this thread is speaking has been
        overtaken by a safety call. Checked before anything is played."""
        gen = getattr(self._local, "gen", None)
        with self._cv:
            return gen is not None and gen != self._gen

    def _register(self, proc) -> None:
        lane = getattr(self._local, "lane", ROUTINE)
        gen = getattr(self._local, "gen", None)
        with self._cv:
            self._procs[lane] = proc
            # The call may have landed between the stale check and Popen.
            if gen is not None and gen != self._gen:
                proc.terminate()

    def _next(self, lane: str):
        """Block until this lane has a line to speak; None once closed."""
        with self._cv:
            while True:
                q = self._lanes[lane]
                ready = q and (lane == PRIORITY or not self._priority_active)
                if ready:
                    self._local.gen = self._gen if lane == ROUTINE else None
                    return q.popleft()
                if self._closed and not q:
                    return None
                self._cv.wait()

    def _run(self, lane: str) -> None:
        self._local.lane = lane
        while True:
            text = self._next(lane)
            if text is None:
                return
            try:
                self._speak(text)
            except Exception:
                log.exception("TTS failed for %r — falling back to say", text)
                if not self._stale():
                    _say_builtin(text, config.TTS_VOICE, config.TTS_RATE,
                                 self._register)
            finally:
                with self._cv:
                    self._procs[lane] = None
                    if lane == PRIORITY and not self._lanes[PRIORITY]:
                        self._priority_active = False
                    self._cv.notify_all()

    def _speak(self, text: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        for w in self._workers:
            w.join(timeout=35)


# Sentence boundary. Kokoro synthesizes a whole utterance before a single
# word can play, so a long line costs its full synthesis time in silence
# (measured: ~1.9 s mean for a two-sentence coaching call). Splitting on
# sentences lets the first one start playing while the rest is generated.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Below this, whole-line synthesis is already quick and splitting would only
# add per-call overhead and a choppy seam.
_STREAM_MIN_CHARS = 90


def _chunks(text: str) -> list:
    """Sentences to synthesize in order, or the whole line if it is short
    enough that streaming would not pay for itself."""
    if len(text) < _STREAM_MIN_CHARS:
        return [text]
    parts = [p for p in _SENTENCE_SPLIT.split(text) if p]
    return parts if len(parts) > 1 else [text]


def _unlink(path) -> None:
    if path is None:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _play_wav_cmd(path: str) -> list:
    """Subprocess command that plays a WAV file to completion and can be
    stopped by terminating the process (how priority calls cut in). macOS
    uses afplay; Windows uses the SoundPlayer in built-in PowerShell, so
    there's nothing extra to install on a tester's machine."""
    if _IS_WIN:
        return ["powershell", "-NoProfile", "-Command",
                f"(New-Object System.Media.SoundPlayer '{path}').PlaySync()"]
    return ["afplay", path]


def _say_builtin(text: str, voice: str, rate: int, register=None) -> None:
    # Text via stdin so nothing is ever parsed as a flag (Jarvis pattern).
    # This is the last-resort fallback when neural TTS is unavailable:
    # macOS `say`, Windows SAPI via PowerShell (default system voice).
    if _IS_WIN:
        script = ("Add-Type -AssemblyName System.Speech;"
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                  "$s.Speak([Console]::In.ReadToEnd())")
        cmd = ["powershell", "-NoProfile", "-Command", script]
    else:
        cmd = ["say", "-v", voice, "-r", str(rate)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True)
    if register:
        register(proc)
    try:
        proc.communicate(text, timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return
    if not _IS_WIN and proc.returncode is not None and proc.returncode > 0:
        # Likely the voice isn't installed — retry with system default.
        # (Negative returncode means we interrupted it — don't retry.)
        subprocess.run(["say", "-r", str(rate)], input=text, text=True, timeout=30)


class SayTTS(_QueuedAnnouncer):
    def __init__(self, voice: str, rate: int):
        self.voice = voice
        self.rate = rate
        super().__init__()

    def _speak(self, text: str) -> None:
        _say_builtin(text, self.voice, self.rate, self._register)


class KokoroTTS(_QueuedAnnouncer):
    def __init__(self, voice: str, speed: float, precache=()):
        self.voice = voice
        self.speed = speed
        self._kokoro = None
        # Fixed phrases rendered ahead of time. These are the safety calls,
        # where the delay before the driver hears anything matters most and
        # sentence streaming cannot help — they are single short sentences,
        # so their whole synthesis (measured: 626 ms median, 982 ms worst)
        # lands in front of the very call that needs to be immediate.
        self._precache = tuple(precache)
        self._cache = {}          # text -> wav path, written by the warmup
        self._cache_lock = threading.Lock()
        # One synthesis at a time: the model is created lazily by whichever
        # thread gets there first, and the phonemizer underneath kokoro is
        # not safe to drive from two threads at once.
        self._synth_lock = threading.Lock()
        super().__init__()
        # Warm the model off-thread so the first real line isn't slow.
        threading.Thread(target=self._warmup, daemon=True).start()

    def _get_kokoro(self):
        # Caller holds _synth_lock.
        if self._kokoro is None:
            from kokoro_onnx import Kokoro
            self._kokoro = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
        return self._kokoro

    def _warmup(self) -> None:
        try:
            with self._synth_lock:
                self._get_kokoro().create("Check.", voice=self.voice,
                                          speed=self.speed, lang="en-gb")
            log.info("kokoro TTS warmed (voice %s)", self.voice)
        except Exception:
            log.exception("kokoro warmup failed — lines will fall back to say")
            return
        for text in self._precache:
            try:
                path = self._synth_wav(text)
            except Exception:
                log.exception("pre-cache failed for %r — it will be "
                              "synthesized on demand instead", text)
                continue
            with self._cache_lock:
                self._cache[text] = path
        if self._precache:
            log.info("pre-rendered %d safety calls", len(self._cache))

    def _synth_wav(self, text: str) -> str:
        """Synthesize one chunk to a temp WAV and return its path."""
        import numpy as np
        with self._synth_lock:
            samples, sr = self._get_kokoro().create(
                text, voice=self.voice, speed=self.speed, lang="en-gb")
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        # mkstemp (not NamedTemporaryFile) because on Windows a still-open
        # NamedTemporaryFile can't be reopened by the player process.
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(pcm.tobytes())
        return path

    def _speak(self, text: str) -> None:
        # Play each sentence while the next one is still being synthesized,
        # so the driver hears the first words after one sentence's worth of
        # synthesis instead of the whole line's. The first chunk is the only
        # one whose synthesis the driver ever waits on; every later chunk is
        # generated under the previous chunk's playback, which runs longer
        # than the synthesis it hides.
        chunks = _chunks(text)
        path, owned = self._wav_for(chunks[0])
        try:
            for i in range(len(chunks)):
                # A safety call arrived while this chunk was synthesizing:
                # drop the line instead of playing it after the call.
                if self._stale():
                    log.info("dropped coaching overtaken by a safety call: %r",
                             text)
                    break
                proc = subprocess.Popen(_play_wav_cmd(path))
                self._register(proc)
                nxt = (None, False)
                if i + 1 < len(chunks):
                    try:
                        nxt = self._wav_for(chunks[i + 1])
                    except Exception:
                        log.exception("chunk synthesis failed — truncating line")
                try:
                    proc.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    proc.kill()
                if owned:
                    _unlink(path)
                path, owned = nxt
                # A priority call terminated playback: drop the rest of this
                # line rather than talking over the safety call that cut in.
                if path is None or (proc.returncode or 0) < 0:
                    break
        finally:
            if owned:
                _unlink(path)

    def _wav_for(self, text: str):
        """(wav path, whether this call owns the file). Pre-rendered phrases
        are shared and must outlive the line that played them."""
        with self._cache_lock:
            hit = self._cache.get(text)
        if hit is not None:
            return hit, False
        return self._synth_wav(text), True

    def close(self) -> None:
        super().close()
        with self._cache_lock:
            for path in self._cache.values():
                _unlink(path)
            self._cache.clear()


def make_announcer(enabled: bool, engine: str, voice: str, rate: int) -> Announcer:
    if not enabled:
        return NullAnnouncer()
    if engine == "kokoro":
        try:
            import kokoro_onnx  # noqa: F401 — availability check only
            import numpy        # noqa: F401
            if not config.KOKORO_MODEL.exists() or not config.KOKORO_VOICES.exists():
                raise FileNotFoundError(f"kokoro models not found at {config.KOKORO_MODEL.parent}")
            from brain import RaceEngineer
            return KokoroTTS(config.KOKORO_VOICE, config.KOKORO_SPEED,
                             precache=RaceEngineer.FLAG_CALLS.values())
        except Exception as e:
            log.warning("neural voice unavailable (%s) — falling back to "
                        "built-in say. Run pip install kokoro-onnx numpy "
                        "for the natural voice.", e)
    return SayTTS(voice, rate)
