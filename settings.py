"""The driver's own engineer settings, chosen on the dashboard.

Kept in APP_DIR/settings.json — next to driver_db.json, never inside the
bundle — so they survive updates and are per machine. Command-line flags
and environment variables set the starting values; anything saved here
wins over them on the next launch.
"""
import json
import logging

import config

log = logging.getLogger("settings")

PATH = config.APP_DIR / "settings.json"

VOICES = {
    "bm_lewis": "Lewis — measured, mid-range",
    "bm_george": "George — deeper, authoritative",
    "bm_daniel": "Daniel — brisk, younger",
    "bm_fable": "Fable — warm, softer",
}

DEFAULTS = {"driverName": "", "voice": "bm_lewis", "radioFx": True,
            "audioDevice": ""}
MAX_NAME = 24


def clean(raw: dict, base: dict | None = None) -> dict:
    """Validated settings: unknown keys dropped, bad values ignored."""
    out = dict(base or DEFAULTS)
    if not isinstance(raw, dict):
        return out
    name = raw.get("driverName")
    if isinstance(name, str):
        # A name is spoken, so keep it to letters and the odd space/dash.
        name = "".join(c for c in name if c.isalpha() or c in " -'").strip()
        out["driverName"] = name[:MAX_NAME]
    voice = raw.get("voice")
    if isinstance(voice, str) and voice in VOICES:
        out["voice"] = voice
    if isinstance(raw.get("radioFx"), bool):
        out["radioFx"] = raw["radioFx"]
    dev = raw.get("audioDevice")
    if isinstance(dev, str):
        out["audioDevice"] = dev.strip()[:120]
    return out


def load(base: dict | None = None) -> dict:
    try:
        return clean(json.loads(PATH.read_text()), base)
    except FileNotFoundError:
        return dict(base or DEFAULTS)
    except (OSError, ValueError):
        log.warning("could not read %s — using defaults", PATH, exc_info=True)
        return dict(base or DEFAULTS)


def save(values: dict) -> None:
    try:
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean(values), indent=2))
        tmp.replace(PATH)
    except OSError:
        log.warning("could not save %s", PATH, exc_info=True)
