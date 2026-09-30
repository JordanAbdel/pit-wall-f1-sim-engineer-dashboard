"""Long-term driver database — session summaries persisted across runs.

One JSON file, one record per session. Enough to say "last time here,
best was a 1:50.5" on connect and to compare sessions in the recap.
"""
import json
import logging
import time

import config

log = logging.getLogger("driver_db")

DB_PATH = config.APP_DIR / "driver_db.json"
MIN_LAPS_TO_ARCHIVE = 2


def load() -> list:
    try:
        return json.loads(DB_PATH.read_text())
    except FileNotFoundError:
        return []
    except Exception:
        log.exception("driver_db unreadable — starting fresh")
        return []


def append(record: dict) -> None:
    records = load()
    records.append(record)
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=1))
    tmp.replace(DB_PATH)
    log.info("archived session: %s", {k: record[k] for k in
                                      ("track_id", "session_type", "laps",
                                       "best_lap_ms") if k in record})


def make_record(track_id, session_type, recap: dict | None,
                recurring=None) -> dict | None:
    if recap is None or recap["laps"] < MIN_LAPS_TO_ARCHIVE:
        return None
    record = {
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "track_id": track_id,
        "session_type": session_type,
        **recap,
    }
    if recurring is not None:
        onset_m, cause, avg, laps = recurring
        record["recurring"] = {"onset_m": onset_m, "cause": cause,
                               "avg_magnitude": avg, "laps": laps}
    return record


def last_for_track(records: list, track_id) -> dict | None:
    """Most recent session here with a best worth quoting. A session where
    every lap was deleted has only a stand-in best, so it is skipped.
    Records from before best_lap_valid existed are taken as they are."""
    for record in reversed(records):
        if (record.get("track_id") == track_id
                and record.get("best_lap_valid", True)):
            return record
    return None
