"""Sentence streaming in the neural TTS path.

Kokoro synthesizes a whole utterance before a single word can play, so a
long coaching line costs its full synthesis time in silence. _speak splits
on sentences and generates the next one under the current one's playback.
The driver must still hear every word, in order — a latency win that drops
half the call is a regression, not an optimization.
"""
import subprocess

import pytest

import speak


class FakePlayer:
    """Stands in for afplay: records what it was handed, never plays."""

    def __init__(self, returncode=0):
        self.paths = []
        self.returncode = returncode

    def popen(self, cmd):
        self.paths.append(cmd[-1])
        return _FakeProc(self.returncode)


class _FakeProc:
    def __init__(self, returncode):
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


class StubKokoro:
    """Records the text of every chunk it is asked to synthesize."""

    def __init__(self):
        self.texts = []

    def create(self, text, voice=None, speed=None, lang=None):
        self.texts.append(text)
        import numpy as np
        return np.zeros(100, dtype="float32"), 24000


@pytest.fixture
def tts(monkeypatch):
    import threading
    t = speak.KokoroTTS.__new__(speak.KokoroTTS)
    t.voice, t.speed = "bm_lewis", 1.0
    t._kokoro = StubKokoro()
    t._init_lanes()  # queue state only — no worker threads
    t._precache = ()
    t._cache = {}
    t._cache_lock = threading.Lock()
    t._synth_lock = threading.Lock()
    return t


@pytest.fixture
def player(monkeypatch):
    p = FakePlayer()
    monkeypatch.setattr(speak.subprocess, "Popen", p.popen)
    return p


# ---- chunking ----

def test_short_line_is_not_split():
    line = "Box this lap."
    assert speak._chunks(line) == [line]


def test_line_below_threshold_stays_whole():
    line = "You lost half a second in the first sector. Brake later."
    assert len(line) < speak._STREAM_MIN_CHARS
    assert speak._chunks(line) == [line]


def test_long_multi_sentence_line_splits_on_sentences():
    line = ("You lost half a second in the first sector. You braked about 70 "
            "metres too early for turn six, Brooklands. Try going deeper.")
    assert speak._chunks(line) == [
        "You lost half a second in the first sector.",
        "You braked about 70 metres too early for turn six, Brooklands.",
        "Try going deeper.",
    ]


def test_long_single_sentence_is_not_split():
    # No sentence boundary to split on — an em-dash is not one, and splitting
    # mid-clause would put an unnatural pause in the middle of a call.
    line = ("That's a one 42 point 6 — half a second slower than your best, "
            "and most of it is going in the first sector somewhere.")
    assert len(line) >= speak._STREAM_MIN_CHARS
    assert speak._chunks(line) == [line]


def test_chunks_preserve_every_word():
    line = ("New best lap — a one 42 point 1. You found that time by braking "
            "later into the braking zone. Keep doing that.")
    assert " ".join(speak._chunks(line)) == line


# ---- streaming playback ----

def test_every_chunk_is_synthesized_and_played(tts, player):
    line = ("You lost half a second in the first sector. You braked about 70 "
            "metres too early for turn six, Brooklands. Try going deeper.")
    tts._speak(line)
    assert tts._kokoro.texts == speak._chunks(line)
    assert len(player.paths) == 3


def test_short_line_plays_exactly_once(tts, player):
    tts._speak("Box this lap.")
    assert tts._kokoro.texts == ["Box this lap."]
    assert len(player.paths) == 1


def test_temp_files_are_cleaned_up(tts, player):
    import os
    line = ("You lost half a second in the first sector. You braked about 70 "
            "metres too early for turn six, Brooklands. Try going deeper.")
    tts._speak(line)
    assert player.paths, "nothing was played"
    assert not [p for p in player.paths if os.path.exists(p)]


def test_interrupted_playback_abandons_the_rest_of_the_line(tts, monkeypatch):
    """A priority safety call terminates the player. The remaining sentences
    must be dropped, not spoken over the top of the call that cut in."""
    p = FakePlayer(returncode=-15)  # SIGTERM, as _interrupt_playback sends
    monkeypatch.setattr(speak.subprocess, "Popen", p.popen)
    line = ("You lost half a second in the first sector. You braked about 70 "
            "metres too early for turn six, Brooklands. Try going deeper.")
    tts._speak(line)
    assert len(p.paths) == 1
    # The second chunk was generated under the first's playback before the
    # interrupt was visible; nothing after it is.
    assert len(tts._kokoro.texts) == 2


def test_chunk_synthesis_failure_truncates_rather_than_raising(tts, player):
    calls = {"n": 0}
    real = tts._kokoro.create

    def flaky(text, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("synthesis blew up")
        return real(text, **kw)

    tts._kokoro.create = flaky
    line = ("You lost half a second in the first sector. You braked about 70 "
            "metres too early for turn six, Brooklands. Try going deeper.")
    tts._speak(line)  # must not raise — the first sentence still goes out
    assert len(player.paths) == 1


# ---- pre-rendered safety calls ----

def test_flag_calls_are_pre_rendered_at_warmup(tts, player):
    """Safety calls are a fixed vocabulary, so their synthesis is paid once
    on the warmup thread rather than in front of the call that needs to be
    immediate."""
    import brain
    tts._precache = tuple(brain.RaceEngineer.FLAG_CALLS.values())
    tts._warmup()
    assert set(tts._cache) == set(tts._precache)


def test_pre_rendered_call_is_played_without_resynthesizing(tts, player):
    line = "Yellow flag ahead — take care."
    tts._precache = (line,)
    tts._warmup()
    synthesized_during_warmup = len(tts._kokoro.texts)

    tts._speak(line)
    assert len(tts._kokoro.texts) == synthesized_during_warmup, \
        "a cached phrase was synthesized again"
    assert len(player.paths) == 1


def test_cached_wav_survives_the_line_that_played_it(tts, player):
    import os
    line = "Safety car's out."
    tts._precache = (line,)
    tts._warmup()
    tts._speak(line)
    tts._speak(line)  # second call must still find the file on disk
    assert os.path.exists(tts._cache[line])
    assert len(player.paths) == 2


def test_uncached_line_still_synthesizes_and_cleans_up(tts, player):
    import os
    tts._precache = ("Safety car's out.",)
    tts._warmup()
    tts._speak("Box this lap.")
    assert "Box this lap." in tts._kokoro.texts
    assert not os.path.exists(player.paths[-1])


def test_failed_precache_entry_falls_back_to_on_demand(tts, player):
    real = tts._kokoro.create

    def flaky(text, **kw):
        if text == "Red flag, red flag — session's stopped.":
            raise RuntimeError("synthesis blew up")
        return real(text, **kw)

    tts._kokoro.create = flaky
    tts._precache = ("Safety car's out.", "Red flag, red flag — session's stopped.")
    tts._warmup()  # must not raise
    assert set(tts._cache) == {"Safety car's out."}


# ---- safety calls vs. routine coaching (two lanes) ----

import threading  # noqa: E402
import time  # noqa: E402

FLAG = "Yellow flag ahead — take care."
COACHING = ("You lost half a second in the first sector. You braked about 70 "
            "metres too early for turn six, Brooklands. Try going deeper.")


def _wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


class BlockingKokoro(StubKokoro):
    """Synthesis of one chosen text hangs until released — a coaching line
    caught mid-generation, which is exactly when there is no player
    process for a safety call to kill."""

    def __init__(self, block_on):
        super().__init__()
        self.block_on = block_on
        self.started = threading.Event()
        self.release = threading.Event()

    def create(self, text, **kw):
        if text == self.block_on:
            self.started.set()
            assert self.release.wait(5), "test never released synthesis"
        return super().create(text, **kw)


@pytest.fixture
def live_tts(tts):
    """The tts fixture with both worker threads running."""
    speak._QueuedAnnouncer.__init__(tts)
    yield tts
    if isinstance(tts._kokoro, BlockingKokoro):
        tts._kokoro.release.set()
    tts.close()


def test_routine_lines_never_push_out_a_queued_safety_call(tts):
    tts.say(FLAG, priority=True)
    for i in range(3):  # more than the routine backlog holds
        tts.say(f"Coaching line {i}.")
    assert list(tts._lanes[speak.PRIORITY]) == [FLAG]
    assert len(tts._lanes[speak.ROUTINE]) == tts.ROUTINE_BACKLOG


def test_safety_call_flushes_queued_coaching(tts):
    tts.say("Coaching line one.")
    tts.say("Coaching line two.")
    tts.say(FLAG, priority=True)
    assert not tts._lanes[speak.ROUTINE]


def test_safety_call_does_not_wait_for_coaching_synthesis(live_tts, player):
    """The flag is played while the coaching line is still being generated
    — not after that line has been synthesized and played in full."""
    tts = live_tts
    first = speak._chunks(COACHING)[0]
    tts._kokoro = BlockingKokoro(block_on=first)
    tts._cache[FLAG] = tts._synth_wav(FLAG)  # pre-rendered, as at warmup
    tts.say(COACHING)
    assert tts._kokoro.started.wait(2)
    tts.say(FLAG, priority=True)
    assert _wait_for(lambda: tts._cache[FLAG] in player.paths), \
        "flag call waited behind a coaching line's synthesis"
    tts._kokoro.release.set()
    assert _wait_for(lambda: not tts._lanes[speak.ROUTINE]
                     and not tts._priority_active)
    time.sleep(0.05)
    # The overtaken coaching line is dropped, not played after the flag.
    assert player.paths == [tts._cache[FLAG]]


def test_coaching_queued_behind_a_safety_call_plays_after_it(live_tts, player):
    tts = live_tts
    tts._cache[FLAG] = tts._synth_wav(FLAG)
    tts.say(FLAG, priority=True)
    tts.say("Box this lap.")
    assert _wait_for(lambda: len(player.paths) == 2)
    assert player.paths[0] == tts._cache[FLAG]


def test_stale_line_registering_a_player_is_cut_off(tts):
    """A safety call can land between the stale check and Popen; the
    player that then registers must be stopped at once."""
    tts._local.gen = tts._gen
    tts.say(FLAG, priority=True)

    class Proc:
        terminated = False

        def terminate(self):
            self.terminated = True

    proc = Proc()
    tts._register(proc)
    assert proc.terminated
