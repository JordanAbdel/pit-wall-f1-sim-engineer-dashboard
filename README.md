# F1 25 Voice Race Engineer

A background app that listens to F1 25's live telemetry from your Xbox,
tracks how your lap and sector times compare to your own bests for the
session, and gives you spoken coaching after each sector and lap —
plus a live "PIT WALL" dashboard in your browser.

Runs on your Mac. Out of the box (no cloud keys) it's the original
personal tool — LOCAL DEV MODE, no account needed. With Supabase +
Stripe configured it becomes a distributable product: email/password
accounts, a $4.99/month subscription paywall (Stripe Checkout), and
per-user cloud storage of every session and lap. Setup:
`SETUP_BILLING.md`.

---

## How it works

```
Xbox (F1 25) ──UDP──▶  Mac listens on port 20777
                          │
                          ├─▶ parses lap/sector times, tyres, fuel
                          ├─▶ compares this lap/sector to your session best
                          ├─▶ speaks a short line ("Purple sector two, three tenths quicker")
                          └─▶ updates the PIT WALL dashboard in your browser
```

Everything resets when you start a new session in-game (leaving the
garage, restarting a session, etc.) — bests, lap history, and the
dashboard all clear automatically so old laps never contaminate a new
run.

---

## Turning it on

1. Open Terminal, go to the project folder:
   ```sh
   cd "/Users/jordanabdel/Documents/Claude/VP/race-engineer"
   ```
2. Start it:
   ```sh
   python3 main.py
   ```
   Uses the natural neural voice by default (models are bundled in
   `models/`). If the `kokoro-onnx`/`numpy` packages aren't installed
   in whatever Python you're running, it says so once and falls back
   to the built-in robotic voice — everything else works the same.
3. You'll see:
   ```
   Listening for F1 25 telemetry on UDP 0.0.0.0:20777 …
   Pit wall dashboard: http://127.0.0.1:8777
   ```
4. Open **http://127.0.0.1:8777** in a browser (Safari/Chrome — leave
   the tab open, it updates live on its own).
5. Get on track in F1 25. The first packet from the game flips the
   dashboard from "NO SIGNAL" to "LIVE", and the terminal starts
   printing sector/lap times as you complete them.

**First time only:** if macOS asks whether to allow Python to accept
incoming network connections, click **Allow** — this is the Xbox's
telemetry reaching your Mac, nothing else.

## Turning it off

Click into the Terminal window running it and press **Ctrl+C**. It
prints `Stopped.` and exits cleanly — the dashboard tab will just stop
updating (safe to leave open or close it).

There's no background daemon or menu bar item — if the Terminal window
is closed, the app is off.

## Restarting for a new session

Just leave it running. It detects a new F1 25 session automatically
(new session UID from the game) and clears bests/history on its own —
no need to restart the app between practice/quali/race sessions.

If you swap to a different track or restart the Xbox entirely, restart
`main.py` too if the dashboard looks stuck (see Troubleshooting).

---

## One-time Xbox setup

In F1 25: **Settings → Telemetry Settings**

| Setting | Value |
|---|---|
| UDP Telemetry | On |
| UDP Broadcast Mode | Off |
| UDP IP Address | your Mac's LAN IP (System Settings → Wi-Fi → Details) |
| UDP Port | 20777 |
| UDP Send Rate | 20 Hz is plenty |
| UDP Format | **2025** |

Mac and Xbox must be on the same Wi-Fi/network. If your Mac's IP
changes (new network, router reboot), update the Xbox setting to match.

---

## What you'll see and hear

- **Terminal**: a line per sector and per lap completed, e.g.
  `[lap 2] S1 30.900  delta -0.300s  best 30.900  ** personal best`
- **Voice**: pit-wall radio register — clipped, delta-first, max two
  short sentences, in a natural British voice. *"Purple, sector one.
  Minus point 3."* … *"Plus point 3, sector two."* … *"New best, one
  50 point 5."* Clean, on-pace sectors get **silence** (anything under
  `CALLOUT_MIN_DELTA_MS` in config.py, default 0.1s, isn't worth air
  time). When the telemetry explains a delta he adds one observation
  (*"Down 9 on top speed."*), plus occasional facts: tyre age, low
  fuel, best trap speed, a radio check when telemetry connects, and an
  *"Off in sector two. Lap's gone."* call instead of tenths when
  you've been through the gravel.
- **Flag calls (priority radio)**: yellow / track clear, safety car,
  VSC, green flag, red flag, blue flag, chequered flag, and your own
  penalties. Flag calls jump the queue and cut off whatever routine
  line is being spoken — safety first.
- **Track map**: the dashboard shows a stylised outline of the current
  circuit with numbered corners (Spa and Silverstone so far — adding a
  track is one data entry in `dashboard.html`'s `TRACK_MAPS`). Your
  session's **weak turns are highlighted on the map** — a pulsing red
  ring on the biggest problem, amber on the rest, with a severity list
  under the map. During a live session a **red dot shows your car's
  position** on the circuit in real time.
- **View modes**: tabs at the top switch between **DASHBOARD** (full
  panels) and **TRACK MAP** (fullscreen map with weak turns + live car
  position — made for a second monitor). The choice is remembered per
  browser.
- **Recap button**: top-right of the dashboard. Opens a session
  breakdown (best lap, theoretical best, weakest/strongest sector,
  session trend, recurring mistake, **worst turns**, consistency) and
  the engineer speaks the same debrief — including the turn-weakness
  breakdown: *"Biggest losses: turn ten, braking early; turn five,
  apex speed; turn one, slow exit. Practice focus: brake later at turn
  ten — 5 laps, about 20 metres."*
- **Race mode (automatic)**: in a race session the reference switches
  from your best lap to **stint pace** (median of your last 5 laps) —
  no nagging about being off a hotlap you set on fresh tyres. Calls
  become race radio: *"P4, one 33 point 5. Gap ahead, 1 point 2."*,
  *"Fastest lap, one 33 point 1."*, *"Pace dropping — plus point 4 on
  your stint."* Corner coaching compares against your recent laps, so
  it flags mistakes and degradation, not fuel load. Practice, quali,
  and Time Trial keep best-lap coaching. Tune the race comment
  threshold via `RACE_CALLOUT_MIN_DELTA_MS` in config.py.
- **Explaining, not just reporting (v2)**: a new best lap comes with
  *why* — *"New best, one 50 point 2. Carried 7 k more through Pouhon,
  and later on the brakes into Fagnes. Stack your best sectors and it's
  a one 49 point 8."* Uncertain diagnoses get softened ("Looks like…"),
  never stated as fact.
- **Engineer memory (v2)**: he remembers previous laps. Repeat a
  mistake and it's *"Same again at Pouhon."*; three laps running gets
  *"That's 3 laps running at Pouhon — brake later."*; fix it and you
  get *"Better at Pouhon."* The most repeated mistake becomes the live
  **advice line** on the dashboard (the centrepiece) and the practice
  focus in the debrief.
- **Live performance summary (v2)**: the current-lap card shows live
  delta to your best lap, a projected lap time, an ATTACK / GOOD /
  BACK OFF pace state, expected next lap, and a rough PB-chance
  estimate. Sector boxes tell stories — where you lost and gained
  (*"− T10 braking · + T15 exit"*). Driver-style chips (LATE BRAKER,
  CONSISTENT, …) appear once the evidence is real.
- **Long-term driver database (v2)**: session summaries persist to
  `driver_db.json`. On connect: *"Last time here, best was a one 50
  point 5."* The debrief compares against your previous session at the
  same track.
- **Live corner coaching**: the moment you exit a corner you clearly
  lost time in, he tells you — *"Braking early into turn ten — go 20
  metres deeper."*, *"Overslowed turn one, 8 down — carry more in next
  lap."* High-confidence, significant losses only, at most one call
  every 10 seconds, never repeating a corner the sector call just
  covered, and off in qualifying. Needs a reference lap on the board
  first.
- **Corner coaching**: when you lose meaningful time in a sector
  (2 tenths or more), he compares your braking zones against your best
  lap and names the worst one — *"Braking early into turn ten, Pouhon,
  15 metres."*, *"Overslowed turn one — 8 k down at apex."*, *"Slow
  exit at the final chicane, throttle sooner."* Needs one full lap on
  the board first (it compares
  against the best lap driven while the app was listening). Named turns
  are per-track lookup tables in `trace.py` (Spa included); other
  tracks get *"the braking zone 2.1 k in"* until their table is added.
- **Dashboard** (http://127.0.0.1:8777): live lap clock, three sector
  boxes (live time ticking, then locked with a delta once complete),
  best lap, theoretical best, tyre compound/wear, fuel, lap-to-lap
  consistency, full lap history, and a pace-trend chart.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Dashboard says "NO SIGNAL" | No packets arriving — check Xbox telemetry is On, IP/port match, both devices on the same network, and you're actually on track (not in a menu). |
| Terminal warns about packet size mismatch | EA changed the packet layout in an update — flag this, the parser needs a fix before times can be trusted. |
| Times don't match the in-game screen | Compare a few laps directly; if consistently off, something's wrong with the parser — stop trusting the coaching until fixed. |
| No voice, but dashboard/terminal work | Run `python3 main.py --no-tts` to confirm intentionally, or check the `say` voice `Daniel` is installed (`say -v '?'` lists installed voices). |
| Dashboard stuck on an old lap after restarting the Xbox/game | Ctrl+C the app and start it again — a fresh process guarantees a clean slate. |
| Port 20777 or 8777 already in use | Another copy of `main.py` is probably already running — check Terminal windows, or `--port` / `--dashboard-port` to use different ones. |

---

## Useful options

```sh
python3 main.py --raw            # Phase 1 check: just log raw packets, no parsing/voice
python3 main.py --no-tts         # dashboard + terminal, no spoken coaching
python3 main.py --no-dashboard   # voice + terminal, skip the web dashboard
python3 main.py --tts-engine say # force the built-in voice (skip neural TTS)
python3 main.py -v               # verbose/debug logging
python3 main.py --port 20778 --dashboard-port 8778   # run on different ports
```

## Testing without the Xbox

Useful for checking the app itself is working before you're at the wheel:

```sh
python3 main.py --no-tts     # terminal 1
python3 sim.py               # terminal 2 — plays a scripted 3-lap stint
python3 sim.py --race        # simulates a race session (stint pace, P4, gaps)
python3 sim.py --flags       # scripts a yellow, safety car, red flag, chequered flag
```

---

## Project layout (for reference)

```
main.py        UDP socket + dispatch loop, console output
packets.py     F1 25 (format 2025) struct parsing, size validation
state.py       packet stream -> sector/lap completion events
analysis.py    events -> deltas vs. session bests + stint pace, debrief stats
brain.py       persona: results -> pit-wall radio lines
speak.py       text-to-speech (bundled Kokoro neural voice, `say` fallback)
trace.py       distance-indexed lap traces, zone comparison, turn names
memory.py      per-corner memory: streaks, fixes, recurring mistakes, style
driver_db.py   long-term session database (driver_db.json)
dashboard.py   local web server for the PIT WALL dashboard
dashboard.html PIT WALL dashboard UI (incl. login/paywall screens)
cloud.py       Supabase auth, subscription gate, cloud session uploads
supabase/      DB schema + Stripe edge functions (see SETUP_BILLING.md)
sim.py         synthetic packet sender for testing without the Xbox
```

v1 scope: sector-level deltas only, no mid-lap warnings. See
`RACE_ENGINEER_APP.md` for the fuller design doc and v2 backlog.
