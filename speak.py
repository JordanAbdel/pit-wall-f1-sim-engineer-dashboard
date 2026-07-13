"""TTS output — swappable engine behind a tiny interface.

Engines:
  "kokoro" — neural TTS (kokoro-onnx), natural British voices. Model
             files are bundled in models/ — self-contained, no sibling
             project needed.
  "say"    — macOS built-in. Instant but robotic; also the automatic
             fallback if kokoro is unavailable or fails.

Speech runs on a worker thread with a small queue so a long utterance
never blocks the packet loop; if lines back up, the oldest is dropped —
stale coaching is worse than no coaching.

say(text, priority=True) is for safety calls (flags, red flag, safety
car): it flushes anything queued and cuts off the line currently being
spoken so the call goes out immediately.
"""
import logging
import os
import queue
import subprocess
import sys
import tempfile
import threading
import wave

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


class _QueuedAnnouncer(Announcer):
    def __init__(self):
        self._q = queue.Queue(maxsize=2)
        self._proc = None          # currently-playing subprocess, if any
        self._proc_lock = threading.Lock()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def say(self, text: str, priority: bool = False) -> None:
        if priority:
            while True:  # flush anything routine that's still queued
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
            self._interrupt_playback()
        try:
            self._q.put_nowait(text)
        except queue.Full:
            try:
                dropped = self._q.get_nowait()
                log.warning("TTS backlog, dropped: %r", dropped)
            except queue.Empty:
                pass
            self._q.put_nowait(text)

    def _interrupt_playback(self) -> None:
        with self._proc_lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()

    def _register(self, proc) -> None:
        with self._proc_lock:
            self._proc = proc

    def _run(self) -> None:
        while True:
            text = self._q.get()
            if text is None:
                return
            try:
                self._speak(text)
            except Exception:
                log.exception("TTS failed for %r — falling back to say", text)
                _say_builtin(text, config.TTS_VOICE, config.TTS_RATE, self._register)

    def _speak(self, text: str) -> None:
        raise NotImplementedError

    def close(self) -> None:
        self._q.put(None)
        self._worker.join(timeout=35)


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
    def __init__(self, voice: str, speed: float):
        self.voice = voice
        self.speed = speed
        self._kokoro = None
        super().__init__()
        # Warm the model off-thread so the first real line isn't slow.
        threading.Thread(target=self._warmup, daemon=True).start()

    def _get_kokoro(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro
            self._kokoro = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
        return self._kokoro

    def _warmup(self) -> None:
        try:
            self._get_kokoro().create("Check.", voice=self.voice,
                                      speed=self.speed, lang="en-gb")
            log.info("kokoro TTS warmed (voice %s)", self.voice)
        except Exception:
            log.exception("kokoro warmup failed — lines will fall back to say")

    def _speak(self, text: str) -> None:
        import numpy as np
        samples, sr = self._get_kokoro().create(
            text, voice=self.voice, speed=self.speed, lang="en-gb")
        pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
        # mkstemp (not NamedTemporaryFile) because on Windows a still-open
        # NamedTemporaryFile can't be reopened by the player process.
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(sr)
                w.writeframes(pcm.tobytes())
            proc = subprocess.Popen(_play_wav_cmd(path))
            self._register(proc)
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def make_announcer(enabled: bool, engine: str, voice: str, rate: int) -> Announcer:
    if not enabled:
        return NullAnnouncer()
    if engine == "kokoro":
        try:
            import kokoro_onnx  # noqa: F401 — availability check only
            import numpy        # noqa: F401
            if not config.KOKORO_MODEL.exists() or not config.KOKORO_VOICES.exists():
                raise FileNotFoundError(f"kokoro models not found at {config.KOKORO_MODEL.parent}")
            return KokoroTTS(config.KOKORO_VOICE, config.KOKORO_SPEED)
        except Exception as e:
            log.warning("neural voice unavailable (%s) — falling back to "
                        "built-in say. Run pip install kokoro-onnx numpy "
                        "for the natural voice.", e)
    return SayTTS(voice, rate)
