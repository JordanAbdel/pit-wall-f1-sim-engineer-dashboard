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

# Cloud backend (Supabase). Absent -> LOCAL DEV MODE: no auth, no paywall,
# no cloud persistence — the app behaves like the original personal tool.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
CHECKOUT_FUNCTION_URL = (f"{SUPABASE_URL}/functions/v1/create-checkout"
                         if SUPABASE_URL else "")

# Network — Xbox must be set to send UDP telemetry to this Mac's LAN IP on this port.
BIND_IP = "0.0.0.0"
PORT = 20777

# Dashboard (pit-wall web UI, served on localhost)
DASHBOARD_PORT = 8777

# TTS — "kokoro" is the natural neural voice, bundled in models/ (self-
# contained, no sibling project needed). Any failure falls back to macOS
# "say" automatically.
TTS_ENGINE = "kokoro"
KOKORO_MODEL = BUNDLE_DIR / "models" / "kokoro-v1.0.onnx"
KOKORO_VOICES = BUNDLE_DIR / "models" / "voices-v1.0.bin"
KOKORO_VOICE = "bm_lewis"   # British males: bm_george (Jarvis), bm_fable, bm_lewis, bm_daniel
KOKORO_SPEED = 1.08         # slightly brisk — race engineers don't dawdle

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
