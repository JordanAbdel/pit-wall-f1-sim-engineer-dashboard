"""Configuration for the F1 25 race engineer."""
import os
import sys
from pathlib import Path

# Two distinct notions of "where files live", because a PyInstaller-frozen
# build unpacks its bundled read-only assets into a temp directory that
# moves every launch — user state must never go there.
#   BUNDLE_DIR — read-only assets shipped with the app (voice models,
#                dashboard.html): PyInstaller's temp extraction dir when
#                frozen, else this file's own directory in dev.
#   APP_DIR    — where the actual executable/script lives, for state that
#                must persist and stay discoverable (.env, driver_db.json,
#                auth.json). Never the temp extraction dir.
IS_FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = (Path(sys.executable).resolve().parent if IS_FROZEN
          else Path(__file__).resolve().parent)
# The Mac .app sets PITWALL_HOME (~/Library/Application Support/PitWall):
# state can't live inside a signed, possibly read-only app bundle.
if os.environ.get("PITWALL_HOME"):
    APP_DIR = Path(os.environ["PITWALL_HOME"]).expanduser()
    APP_DIR.mkdir(parents=True, exist_ok=True)

# Load KEY=VALUE pairs from .env (real environment wins). This is where the
# Supabase keys go — see SETUP_BILLING.md. APP_DIR/.env is the dev override;
# frozen builds carry a copy baked inside the bundle (see PitWall.spec) so
# licensing config can't be stripped off by deleting a file next to the exe.
for _env_file in (APP_DIR / ".env", BUNDLE_DIR / ".env"):
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

# Licence API (the Cloudflare Worker in cloudflare/). Absent -> LOCAL DEV
# MODE: no auth, no paywall — the app behaves like the original personal
# tool. SUPABASE_URL/SUPABASE_ANON_KEY are still honoured for older .env
# files; the Worker answers the same paths, so either works.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
API_URL = (os.environ.get("PITWALL_API_URL", "").rstrip("/")
           or (SUPABASE_URL if SUPABASE_ANON_KEY else ""))
# Ed25519 public key (base64, 32 raw bytes) that verifies the server-signed
# offline licence — see supabase/functions/issue-license. Without it there
# is no offline grace: the app must reach the licence server to run.
LICENSE_PUBLIC_KEY = os.environ.get("LICENSE_PUBLIC_KEY", "")

# Network — Xbox must be set to send UDP telemetry to this Mac's LAN IP on
# this port. The UDP format setting is not important: packet formats 2020
# through 2025 are all decoded (see formats.py).
BIND_IP = "0.0.0.0"
PORT = 20777

# Dashboard (pit-wall web UI, served on localhost)
DASHBOARD_PORT = 8777
# Second-screen access for phones/tablets on the same network. Off unless
# the user turns it on (dashboard PHONE button, or --lan); each device must
# pair with a code shown on this computer. See lan.py.
DASHBOARD_LAN_PORT = 8778

# TTS — "kokoro" is the natural neural voice, bundled in models/ (self-
# contained, no sibling project needed). Any failure falls back to macOS
# "say" automatically.
TTS_ENGINE = "kokoro"
KOKORO_MODEL = BUNDLE_DIR / "models" / "kokoro-v1.0.onnx"
KOKORO_VOICES = BUNDLE_DIR / "models" / "voices-v1.0.bin"
KOKORO_VOICE = os.environ.get("PITWALL_VOICE") or "bm_lewis"
#                           # British males: bm_george (Jarvis), bm_fable, bm_lewis, bm_daniel
KOKORO_SPEED = 1.08         # slightly brisk — race engineers don't dawdle

# The engineer is heard over team radio (band-limited, compressed, with
# press-to-talk static — see radio.py). PITWALL_RADIO=0 for the clean voice.
RADIO_FX = os.environ.get("PITWALL_RADIO", "1").strip().lower() not in ("0", "off", "no", "false")

# What the engineer calls you on the radio, now and then. Blank = no name.
DRIVER_NAME = os.environ.get("PITWALL_DRIVER_NAME", "").strip()

# Where the engineer's voice plays: blank = the system default output, or
# (part of) a device name such as "BlackHole 2ch" to send it into a Discord
# call that reaches the headset on an Xbox. See docs/XBOX_AUDIO.md.
AUDIO_DEVICE = os.environ.get("PITWALL_AUDIO_DEVICE", "").strip()

# say fallback
TTS_VOICE = "Daniel"        # British voice; falls back to system default if missing
TTS_RATE = 190              # words per minute

# Radio triage — deltas smaller than this are not worth air time: the
# engineer stays silent rather than forcing a comment. Tune to taste.
CALLOUT_MIN_DELTA_MS = 100

# In races the reference is stint pace (median of recent laps), not the
# session-best lap, and the bar for commenting is higher — normal race
# variance shouldn't trigger coaching.
RACE_CALLOUT_MIN_DELTA_MS = 300
