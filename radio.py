"""Team-radio sound — turns a clean TTS take into a pit-wall transmission.

A real engineer is heard through a narrow, hard-compressed digital radio:
no bass, no air, a forward nasal midrange, a little grit when he leans on a
word, an open-mic hiss under the voice, and a click plus a burst of static
when the button is pressed and released. That chain is what makes the voice
read as "my engineer on the radio" rather than "a computer talking", so it
is applied to every line before it is played.

Everything here is plain numpy on whole buffers (lines are a few seconds
long), so a take costs a few milliseconds — nothing next to synthesis.

Intelligibility wins every trade-off: safety calls go through the same
chain, so the band stays wide enough (300 Hz – 3.4 kHz, the telephone band)
and the noise stays well under the voice.
"""
import numpy as np

# The voice band of a radio link. Below LOW the voice loses its chest and
# above HIGH it loses its air — both are what makes radio sound like radio.
LOW_HZ = 320.0
HIGH_HZ = 3400.0
PRESENCE_HZ = 1900.0   # the nasal "honk" every team radio has
PRESENCE_DB = 4.5

NOISE_DB = -38.0       # open-mic hiss under the voice, relative to full scale
CLICK_MS = 4
OPEN_BURST_MS = 55     # static as the button goes down
CLOSE_BURST_MS = 110   # static tail as it comes back up
PREROLL_MS = 70        # carrier before the first word
POSTROLL_MS = 60       # carrier after the last word


def _db(x: float) -> float:
    return 10 ** (x / 20)


def _band_response(n: int, sr: int, low: float, high: float,
                   presence_db: float = PRESENCE_DB) -> np.ndarray:
    """Magnitude response for an rfft of length n: 4th-order-like high and
    low pass skirts plus a broad presence bump."""
    f = np.fft.rfftfreq(n, 1 / sr)
    f[0] = 1e-3
    hp = 1 / np.sqrt(1 + (low / f) ** 8)
    lp = 1 / np.sqrt(1 + (f / high) ** 8)
    bump = 1 + (_db(presence_db) - 1) * np.exp(
        -0.5 * (np.log2(f / PRESENCE_HZ) / 0.7) ** 2)
    return hp * lp * bump


def band_limit(x: np.ndarray, sr: int, low: float = LOW_HZ,
               high: float = HIGH_HZ, presence_db: float = PRESENCE_DB) -> np.ndarray:
    """Zero-phase band-pass via one FFT. Padded so the circular wrap of the
    FFT can't smear the end of a word onto its start."""
    if len(x) == 0:
        return x
    n = 1 << int(np.ceil(np.log2(len(x) + sr // 10)))
    spec = np.fft.rfft(x, n)
    return np.fft.irfft(spec * _band_response(n, sr, low, high, presence_db),
                        n)[:len(x)]


def _smooth(x: np.ndarray, win: int) -> np.ndarray:
    """Centred moving average that keeps the input's length, however short
    the input (np.convolve's "same" mode does not, for a long window)."""
    win = max(1, min(win, len(x)))
    c = np.concatenate([[0.0], np.cumsum(x)])
    lo = np.clip(np.arange(len(x)) - win // 2, 0, len(x))
    hi = np.clip(lo + win, 0, len(x))
    return (c[hi] - c[lo]) / np.maximum(hi - lo, 1)


def _envelope(x: np.ndarray, sr: int, ms: float) -> np.ndarray:
    return np.sqrt(_smooth(x * x, int(sr * ms / 1000)) + 1e-12)


def compress(x: np.ndarray, sr: int, threshold_db: float = -34.0,
             ratio: float = 6.0) -> np.ndarray:
    """Heavy broadcast-style compression: everything above the threshold is
    squashed, then made up, so quiet syllables and loud ones land at nearly
    the same level — the flat, in-your-face delivery of a radio call."""
    env = _envelope(x, sr, 12)
    thr = _db(threshold_db)
    gain = np.ones_like(env)
    over = env > thr
    gain[over] = (env[over] / thr) ** (1 / ratio - 1)
    # Smooth the gain so it pumps like a compressor, not like a fuzz pedal.
    y = x * _smooth(gain, int(sr * 0.006))
    peak = np.max(np.abs(y)) or 1.0
    return y / peak


def saturate(x: np.ndarray, drive: float = 2.2) -> np.ndarray:
    """Soft clipping — the grit of a small speaker and a cheap mic preamp."""
    return np.tanh(x * drive) / np.tanh(drive)


def crush(x: np.ndarray, sr: int, target_sr: int = 11025, bits: int = 10,
          mix: float = 0.35) -> np.ndarray:
    """A touch of digital codec: sample-and-hold to a lower rate and coarser
    quantization, blended in under the clean signal."""
    step = max(1, round(sr / target_sr))
    held = np.repeat(x[::step], step)[:len(x)]
    q = 2 ** (bits - 1)
    held = np.round(held * q) / q
    return (1 - mix) * x + mix * held


def _noise(n: int, sr: int, rng: np.random.Generator, level_db: float) -> np.ndarray:
    if n <= 0:
        return np.zeros(0)
    hiss = band_limit(rng.standard_normal(n), sr, 400.0, 3800.0, 0.0)
    hiss /= np.sqrt(np.mean(hiss ** 2)) + 1e-12
    # Slow wobble, so the carrier breathes instead of sitting dead flat.
    t = np.arange(n) / sr
    wobble = 1 + 0.25 * np.sin(2 * np.pi * rng.uniform(0.4, 1.3) * t
                               + rng.uniform(0, 2 * np.pi))
    return hiss * wobble * _db(level_db)


def _burst(sr: int, ms: float, rng: np.random.Generator, level_db: float,
           rising: bool) -> np.ndarray:
    """Static burst with a fast attack and an exponential decay (or the
    mirror image for the open, which swells into the carrier)."""
    n = int(sr * ms / 1000)
    burst = _noise(n, sr, rng, 0.0)
    env = np.exp(-np.linspace(0, 5, n))
    if rising:
        env = env[::-1] * np.linspace(0.3, 1, n)
    return burst * env * _db(level_db)


def _click(sr: int, rng: np.random.Generator, level_db: float = -9.0) -> np.ndarray:
    n = int(sr * CLICK_MS / 1000)
    t = np.arange(n) / sr
    tone = np.sin(2 * np.pi * rng.uniform(1100, 1500) * t)
    return tone * np.exp(-t * 1400) * _db(level_db)


def transmission(voice: np.ndarray, sr: int, rng: np.random.Generator | None = None,
                 key_up: bool = True, key_down: bool = True) -> np.ndarray:
    """The full chain: one clean TTS take in, one radio transmission out.

    key_up / key_down say whether this buffer carries the press (start) and
    release (end) of the button. A long line streamed sentence by sentence
    is one transmission: the first chunk gets the press, the last the
    release, and the ones in between only the carrier.
    """
    rng = rng or np.random.default_rng()
    x = np.asarray(voice, dtype=np.float64)
    if len(x) == 0:
        return x.astype(np.float32)
    peak = np.max(np.abs(x)) or 1.0
    x = x / peak

    x = band_limit(x, sr)
    x = compress(x, sr)
    x = saturate(x, rng.uniform(1.9, 2.5))
    x = crush(x, sr)
    # Clipping and the codec put a little energy back outside the band.
    x = band_limit(x, sr, LOW_HZ * 0.9, HIGH_HZ * 1.05, 0.0)
    x *= _db(-2.0) / (np.max(np.abs(x)) or 1.0)

    pre = int(sr * PREROLL_MS / 1000) if key_up else int(sr * 0.015)
    post = int(sr * POSTROLL_MS / 1000) if key_down else int(sr * 0.015)
    out = np.concatenate([np.zeros(pre), x, np.zeros(post)])
    out += _noise(len(out), sr, rng, NOISE_DB + rng.uniform(-2, 2))

    if key_up:
        click = _click(sr, rng)
        burst = _burst(sr, OPEN_BURST_MS, rng, -20.0, rising=False)
        out[:len(burst)] += burst
        out[:len(click)] += click
    if key_down:
        tail = _burst(sr, CLOSE_BURST_MS, rng, -16.0, rising=False)
        click = _click(sr, rng, -12.0)
        out = np.concatenate([out, tail])
        out[-len(tail) - len(click):-len(tail)] += click

    # Fades of a few ms so the start and end never pop.
    fade = int(sr * 0.003)
    out[:fade] *= np.linspace(0, 1, fade)
    out[-fade:] *= np.linspace(1, 0, fade)
    return np.clip(out, -1.0, 1.0).astype(np.float32)
