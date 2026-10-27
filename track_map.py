"""Circuit outlines recorded from your own driving.

The hand-drawn outlines in dashboard.html are stylised sketches and several
of them barely resemble the real circuit. The Motion packet already carries
the car's world position every frame, so a lap of driving is an exact trace
of the track — no artwork, no licensing, and it is automatically right for
every circuit and every game version, including layouts that changed between
game years (Albert Park was revised for 2022).

One clean lap per track is recorded, normalised into SVG viewBox coordinates
and cached in track_maps.json next to the app. The dashboard prefers a
recorded map and falls back to the built-in sketch until one exists.
"""
import json
import logging

import config

log = logging.getLogger("trackmap")

STORE = config.APP_DIR / "track_maps.json"

# Sample spacing along the lap. 15 m keeps a 5-6 km circuit around 350-400
# points — enough that hairpins stay sharp, small enough to stay legible.
SAMPLE_M = 15.0
MIN_POINTS = 60

VIEW_W, VIEW_H = 420.0, 280.0
PAD = 18.0


# A lap is only worth drawing if it was recorded continuously from the line
# to the flag. Time Trial restarts and flashbacks rewind lap distance mid-lap;
# stitching the fragments together produces a path that teleports across the
# circuit, which is what a partial recording looks like on screen.
START_WITHIN_M = 150.0      # first sample must be near the start line
COVERAGE = 0.90             # must reach at least this much of the lap
MAX_GAP_M = 90.0            # no missing chunk bigger than this


class MapRecorder:
    """Collects (distance, x, z) world positions for a single continuous lap."""

    def __init__(self):
        self.samples = []       # (lap_distance, x, z), strictly increasing
        self._last_d = None

    def reset(self):
        self.samples = []
        self._last_d = None

    @property
    def points(self):
        return [(x, z) for _d, x, z in self.samples]

    def add(self, lap_distance: float, x: float, z: float) -> None:
        # Only ever move forwards, and only every SAMPLE_M — a stationary car
        # must not pile up points, and a reversing one must not double back.
        if lap_distance is None or lap_distance < 0:
            return
        if self._last_d is not None and lap_distance - self._last_d < SAMPLE_M:
            return
        self._last_d = lap_distance
        self.samples.append((lap_distance, x, z))

    def gaps(self) -> float:
        """Largest jump in lap distance between consecutive samples."""
        if len(self.samples) < 2:
            return float("inf")
        return max(b[0] - a[0] for a, b in zip(self.samples, self.samples[1:]))

    def complete(self, track_length: int | None) -> bool:
        """Whether this is one clean lap, not a stitched-together fragment."""
        if len(self.samples) < MIN_POINTS:
            return False
        if self.samples[0][0] > START_WITHIN_M:
            return False            # joined partway round
        if self.gaps() > MAX_GAP_M:
            return False            # a restart or flashback tore a hole in it
        if track_length:
            return self.samples[-1][0] >= COVERAGE * track_length
        return True

    @property
    def usable(self) -> bool:
        return self.complete(None)


def to_path(points) -> dict | None:
    """Normalise world metres into an SVG path that fits the viewBox,
    preserving aspect ratio so the circuit is not stretched."""
    if len(points) < MIN_POINTS:
        return None
    xs = [p[0] for p in points]
    zs = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_z, max_z = min(zs), max(zs)
    span_x = max_x - min_x
    span_z = max_z - min_z
    if span_x <= 0 or span_z <= 0:
        return None

    scale = min((VIEW_W - 2 * PAD) / span_x, (VIEW_H - 2 * PAD) / span_z)
    # Centre whatever the aspect ratio leaves over.
    off_x = (VIEW_W - span_x * scale) / 2
    off_z = (VIEW_H - span_z * scale) / 2

    def project(x, z):
        return (round((x - min_x) * scale + off_x, 1),
                # SVG y grows downwards; world z grows "up" the map
                round((max_z - z) * scale + off_z, 1))

    pts = [project(x, z) for x, z in points]
    body = " ".join(f"L {x} {y}" for x, y in pts[1:])
    return {
        "path": f"M {pts[0][0]} {pts[0][1]} {body} Z",
        "sf": list(pts[0]),
        "points": len(pts),
    }


def load() -> dict:
    try:
        if STORE.exists():
            return json.loads(STORE.read_text())
    except Exception:
        log.warning("could not read %s — starting fresh", STORE, exc_info=True)
    return {}


def save(maps: dict) -> None:
    try:
        STORE.write_text(json.dumps(maps, indent=1))
    except Exception:
        log.warning("could not write %s", STORE, exc_info=True)


# Game years drive different layouts under one track id (Singapore before
# and after 2023, Catalunya with and without its final chicane), so a map is
# only reused when its lap length matches the session's.
SAME_LAYOUT_M = 50


def _key(maps: dict, track_id, track_length):
    """The store key for this track's layout: the bare id for the first one
    recorded (and for maps saved before layouts were told apart), the id
    plus the lap length for any other layout."""
    key = str(track_id)
    entry = maps.get(key)
    if entry is None or not track_length or not entry.get("trackLength"):
        return key
    if abs(entry["trackLength"] - track_length) <= SAME_LAYOUT_M:
        return key
    return f"{track_id}:{track_length}"


def lookup(maps: dict, track_id, track_length=None):
    """The recorded map for this track and layout, or None."""
    if track_id is None:
        return None
    return maps.get(_key(maps, track_id, track_length))


def record(maps: dict, track_id: int, points, track_length: int | None = None):
    """Store a lap's outline for track_id. Returns the entry, or None if the
    lap was unusable. An existing map is kept — one good lap is enough."""
    if track_id is None:
        return None
    key = _key(maps, track_id, track_length)
    if key in maps:
        return maps[key]
    entry = to_path(points)
    if entry is None:
        return None
    if track_length:
        entry["trackLength"] = track_length
    maps[key] = entry
    save(maps)
    log.info("recorded track map for track %s from %d points",
             key, entry["points"])
    return entry
