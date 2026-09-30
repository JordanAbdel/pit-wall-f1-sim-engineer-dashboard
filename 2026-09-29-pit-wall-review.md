# PitWall review — 2026-09-29

Scope: `race-engineer/` on branch `multi-format-telemetry` at `ff59ec7`, plus the uncommitted `speak.py` streaming/pre-cache work and `tests/test_speak.py`. Everything below comes from reading the source, running the tests, and checking `logs/pitwall.log`. I did not play any laps against a real console.

Tests: **158 passed** under `build-venv/bin/python -m pytest tests`. Running plain `python3 -m pytest` from the repo root **errors during collection** because it walks into `tester-package/`. Under system Python, 10 `test_speak` tests fail because `numpy` isn't installed, which is an environment problem, not a code bug.

---

## Step 1 — Map

The brief assumed a hosted web app (websockets, multi-user backend, frontend component tree). PitWall isn't that. It's a **single-user desktop process** that serves one local HTML page. Supabase and Stripe are used **only for licensing**. The review is scaled to the app that actually exists.

| Layer | Where | Notes |
|---|---|---|
| Entry point | `main.py:725` `main()` | argparse, logging, socket bind, cloud check, dashboard start, then a blocking `recvfrom` loop (`main.py:800-815`). Frozen builds come from PyInstaller (`PitWall.spec`). The Windows build is a manual GH Action (`.github/workflows/windows-build.yml`). |
| UDP listener | `main.py:749-751` | `0.0.0.0:20777`, 1 s timeout, 2048-byte buffer, single thread. Errors in each packet are caught and throttled (`main.py:806-815`). |
| Format dispatch | `main.py:145-160`, `formats.py:456` | Reads `m_packetFormat` (first uint16), then `codec_for(fmt)` picks one of `Codec2020…Codec2025`. It switches per packet, so a mid-session format change is handled. |
| Parsers | `formats.py` (per-year codecs), `packets.py` (canonical F1 25 NamedTuples + F1 25 structs) | Every per-car struct is checked against the official packet totals at import (`formats.py:467-500`, `packets.py:390-401`). Packets are size-validated before parsing (`formats.py:74-83`, `main.py:174-183`). |
| State / events | `state.py` (`SessionState` turns Lap Data into SectorComplete/LapComplete), `analysis.py` (deltas, stint pace, recap), `trace.py` (distance-indexed traces, braking-zone diff, turn names for 24 tracks), `memory.py` (repeat mistakes), `reference.py` (TT rival / benchmark target / self) | All mutable state lives on the `Engineer` in `main.py:71`. A session reset happens on a *stable* session-UID change (`main.py:188-220`). |
| Voice | `brain.py` (phrasing), `speak.py` (Kokoro neural TTS → temp WAV → `afplay`/PowerShell; `say`/SAPI fallback; queue size 2; priority interrupt) | See the dedicated TTS section. |
| API / "websocket" layer | `dashboard.py` | stdlib `ThreadingHTTPServer` on **127.0.0.1:8777**. `GET /`, `/state.json`, `/config.json`, `/auth/state`; `POST /recap`, `/auth/session`, `/auth/refresh`, `/auth/logout`. Host/Origin allow-list for CSRF and DNS rebinding (`dashboard.py:110-132`). **No websockets:** the page polls `/state.json` at 5 Hz (`dashboard.html:1150`). |
| Frontend | `dashboard.html` (1,421 lines, one file, vanilla JS, template-string `innerHTML` renders) | Views: dashboard / fullscreen track map, recap modal, onboarding wizard, FAQ, auth gate/paywall. Clock is interpolated client-side at 20 Hz (`tickClock`). |
| Auth | `cloud.py` + dashboard supabase-js (loaded from `esm.sh`) | The browser logs in with email/password, then POSTs tokens to the local app. The app stores the refresh token in `auth.json` (0600) and checks `profiles.subscription_status` **once at startup**. Offline grace is 7 days via `license.json`. |
| Billing | `supabase/functions/create-checkout`, `supabase/functions/stripe-webhook`, `supabase/migrations/…initial_schema.sql` | One $4.99/mo plan. The webhook maps Stripe events to `profiles.subscription_status`. The `sessions`/`laps` tables are unused. |
| Persistence | `driver_db.json` (session summaries), `track_maps.json` (recorded outlines), `auth.json`, `license.json`, `logs/pitwall.log` (rotating) | All local, next to the executable (`config.APP_DIR`). |

**What I couldn't find or verify**
- `RACE_ENGINEER_APP.md`, which README.md mentions as the design doc and v2 backlog. It isn't in the repo.
- The deployed Supabase project: whether the migration was applied as written, the email-confirm setting, and whether webhook secrets are set. I only reviewed the checked-in SQL/TS.
- Real F1 22/23/24 captures. The codec tests use synthetic packets built from the same struct definitions, so they prove internal consistency, not agreement with the game. Only F1 25 (and a 2020-format run, per the log) has touched real hardware.
- A macOS build/signing pipeline. Only the Windows workflow exists, and `PitWall.spec` has `codesign_identity=None`.

---

## Step 2 — F1 22–25 compatibility audit

### What's right

I checked the header layouts and the totals of every size-asserted packet against the published EA specs:

- **Header:** 24 bytes for 2022 (`formats.py:212`) and 29 bytes for 2023+ (`packets.py:49`). Both correct.
- **Lap Data per car:** 43 B (F1 22), 50 B (F1 23, which added sector minute-parts and uint16 deltas), 57 B (F1 24/25, which added delta minute-parts and speed-trap fields). All correct. *Doc nit:* `packets.py:3-6,74` says F1 25 added the speed-trap fields. They arrived in **F1 24**. The code is right because 2024 and 2025 share the struct.
- **Car Status:** 47 B (22) and 55 B (23+, which added `enginePowerICE/MGUK`). Correct.
- **Car Damage:** 42 B (22/23/24) and 46 B (25, which added tyre blisters). Correct.
- **Session History:** 11 B/lap (22) and 14 B/lap (23+). Correct.
- **Car Telemetry:** 60 B plus 3 trailing bytes. **Motion:** 60 B/car, plus 120 trailing bytes in 2022. Correct.
- **Time Trial:** set is 24 B, 2024+ only. Correct.
- **Session-type renumbering:** Race moves 10→15 and TT 13→18 in F1 24 (`formats.py:422-445`). Correct and tested.
- **Packet totals in `SIZES`:** match the specs for 2022, 2023, 2024 and 2025. The one exception is the F1 22 Event packet: the code says 36, while the spec and real traffic say **40** (`logs/pitwall.log`: "Event packet is 40 bytes, spec says 36"). It's harmless now because events use a length floor (`formats.py:80-81`), but `Codec2022.SIZES[3]` should be corrected to 40.

### Year × packet matrix

Legend:
- ✅ supported: validated and decoded for everything the app uses
- 🟡 partial: validated, only some fields or cases used
- ⬜ missing: size-validated but never decoded
- ❌ broken
- — the packet doesn't exist in that year

| ID | Packet | F1 22 | F1 23 | F1 24 | F1 25 | Evidence |
|---|---|---|---|---|---|---|
| 0 | Motion | 🟡 world pos only | 🟡 | 🟡 | 🟡 | `formats.py:150-156`, `main.py:300-305` |
| 1 | Session | 🟡 lead block, marshal zones, SC status. No weather forecast, pit window, assists, `isSpectating` | 🟡 | 🟡 | 🟡 | `formats.py:116-131` |
| 2 | Lap Data | 🟡 player only; gap-ahead fields don't exist in 22, so race "gap to car ahead" is silent | ✅ player only | ✅ player only | ✅ player only | `formats.py:301-315, 363-380`, `packets.py:123`. **Spectator ❌**, see C-3 |
| 3 | Event | 🟡 PENA, CHQF only. RDFL/SCAR don't exist in 22, so **red flags are never called** | 🟡 RDFL, CHQF, PENA (SC comes from Session) | 🟡 + SCAR | 🟡 + SCAR | `main.py:474-491`. Unused: FTLP, SPTP, OVTK, COLL, FLBK, DTSV/SGSV, LGOT |
| 4 | Participants | ⬜ | ⬜ | ⬜ | ⬜ | no names, teams, or "Your Telemetry" restriction |
| 5 | Car Setups | ⬜ | ⬜ | ⬜ | ⬜ | |
| 6 | Car Telemetry | 🟡 speed/throttle/brake/gear | 🟡 | 🟡 | 🟡 | `formats.py:141-143`. Tyre/brake temps and pressures unread |
| 7 | Car Status | ✅ decoded; UI uses fuel/compound/age only | ✅ | ✅ | ✅ | `formats.py:317-330`, `packets.py:307` |
| 8 | Final Classification | ⬜ | ⬜ | ⬜ | ⬜ | |
| 9 | Lobby Info | ⬜ | ⬜ | ⬜ | ⬜ | |
| 10 | Car Damage | 🟡 wear only, **averaged across 4 corners** | 🟡 | 🟡 | 🟡 blisters ignored | `formats.py:160-164`, `main.py:295-299` |
| 11 | Session History | ✅ player backfill and cross-check | ✅ | ✅ | ✅ | `formats.py:181-194, 385-390`, `main.py:605-652` |
| 12 | Tyre Sets | — | ⬜ | ⬜ | ⬜ | strategy data unused |
| 13 | Motion Ex | — (extras trail Motion, ignored) | ⬜ | ⬜ | ⬜ | |
| 14 | Time Trial | — | — | ✅ rival only | ✅ rival only | `reference.py:62-69` |
| 15 | Lap Positions | — | — | — | ⬜ | |

### Enums and IDs

- **Track IDs** (`packets.py:148-154`) are missing IDs that the F1 22–25 specs send: **1 Paul Ricard**, **28 Portimão**, and **39/40/41 Silverstone/Austria/Zandvoort Reverse (F1 25)**. Unknown IDs fall back to the game name in the header, which is safe. They also get no benchmark, no turn names, and no sketch map. The recorded map still works because it's keyed by ID. Reverse layouts correctly *don't* inherit the forward-direction turn tables, but they need their own entries.
- **Tyre compounds:** the visual set 16/17/18/7/8 is correct for all four years (`main.py:50`). The actual compound (C0–C6) isn't shown.
- **Team IDs:** not used, because Participants isn't parsed. That's fine today, but it's a blocker for any standings or gap feature.
- **FIA flag values:** blue=2, yellow=3 (`packets.py:181`). Correct for all years.
- **Safety-car status:** 0–3 (`packets.py:180`). Correct.

### Robustness

| Case | Behaviour | Verdict |
|---|---|---|
| Unknown format (e.g. 2026) | Logged once and dropped (`main.py:150-156`) | ✅ safe, but the user sees only NO SIGNAL (see H-8) |
| Unknown packet ID / ID from a newer year | `SIZES.get()` → `None` → accepted, then no branch matches → ignored | ✅ |
| Wrong length | Warned once per ID, whole type skipped (`main.py:174-183`) | ✅ safe; ⚠ **silent to the user.** A game patch that resizes Lap Data turns the app into a mute dashboard with only a log line |
| Truncated Event details (SCAR/PENA) | `IndexError` caught by the loop's catch-all | 🟡 works by accident. Guard the length |
| Mid-session format change | Codec swaps per packet; state kept | ✅ |
| Player index 255 (spectating / some online lobby states) | `unpack_from` past buffer → `struct.error` on **every** Lap/Status/Telemetry/Damage packet. Logs 50 tracebacks, then every 500th | ❌ (C-3) |
| Menu UID churn | 20-packet stability gate (`main.py:57, 188-196`) | ✅ |
| Flashback / TT restart | Distance-rewind detection resets trace and map (`main.py:229-238`); `state._resync` | ✅ |
| Pit-lane start/finish crossing | `state.py:111-113` resyncs while `pit_status != 0`, so **the in-lap is never emitted** at tracks where the line is in the pit lane | ❌ (H-5) |

---

## Step 3 — Backend review

**Performance.** `PERF.md` measured 3.4 µs per packet (~298k packets/s). At the game's 20–60 Hz × ~16 packet types (≈ 300–1,000 packets/s) that's under 0.5 % of a core. I agree with the "don't optimise" verdicts there. Two real latency sources remain:
- Synchronous disk writes on the UDP thread (`driver_db.append` at session change, `track_map.record` at the first clean lap). Small and rare.
- **TTS.** This is where the user-visible latency lives. See the TTS section.

Browser latency is ≤200 ms of polling plus render. The client-side clock interpolation hides it for the lap timer. SSE (`text/event-stream`) would cut the delay and the empty polls, but it isn't urgent.

**Dropped packets.** The socket uses the default `SO_RCVBUF`, and the loop never blocks for long, so real drops are unlikely. Still, set `SO_RCVBUF` to about 1 MB for headroom and count `frame_identifier` gaps so drops become observable.

**Memory growth.** Everything grows only by laps. The lap list, `_trace_window` (4 laps), memory zones and `driver_db.json` are all bounded by session length and small. No leaks found. `driver_db.json` is loaded and rewritten whole on every archive, which is fine at hobby scale.

**Threading.**
- The UDP thread owns the engine state.
- HTTP threads read through `Dashboard._lock`. The exception is `POST /recap`, which calls `Engineer.speak_recap` (`main.py:433`) and reads `memory`/`brain` state **from the HTTP thread** while the UDP thread mutates it. It's a low-probability race: worst case a `dict changed size` exception gets logged. Fix by pushing it onto a queue that the UDP loop drains.
- The Kokoro model is lazily created by two threads without a lock (`speak.py:197-201`). See the TTS section.

**Multi-user / session lifecycle.**
- N/A for multi-tenant: one process, one driver.
- Session lifecycle is solid: stable-UID reset, Session History backfill, and archive on exit.
- A crash or `kill -9` loses the whole session, because archiving only happens on UID change or clean exit. Checkpoint after each lap.

**Persistence correctness.**
- `driver_db` and `benchmarks` are keyed by `track_id` only. They ignore **game year, formula (F1 / F2 / F1 World / Classic) and weather**. So "Last time you were here, your best was…" can compare an F1 25 dry lap to an F1 22 wet F2 lap (`driver_db.py:56-60`, `reference.py:58-60`).
- Recaps count invalid laps. See C-1.

**Error handling / logging.**
- The per-packet catch-all is good.
- `excepthook` into a rotating file is good.
- Port-in-use crashes with a raw traceback (`main.py:750`; seen in `logs/pitwall.log` on 2026-08-30 17:00:51). The dashboard port has the same problem (`dashboard.py:203`).

**Tests and config.**
- The 158 tests are good quality, covering codecs, brain phrasing and TTS streaming.
- Gaps: no real-capture fixtures, no pytest config, no CI running tests, unpinned `requirements.txt`, and no dev requirements.

---

## Step 4 — Frontend & UI/UX

**Glanceability.** The hierarchy is inverted for racing:
- The **lap clock** is the largest element (`dashboard.html:52`, up to 84 px), but it's the one number a driver never needs mid-lap.
- **Δ BEST** is 22 px, and it's the number that matters.

For a second screen at arm's length, make Δ the hero with a full-width colour bar, then sector state, then projected lap. The `BACK OFF` pace label (`dashboard.html:713`) is also bad coaching copy. Being 0.3 s down doesn't mean "back off". Use `OFF PACE`, or just show the delta.

**Layout / responsiveness.**
- There are **no `@media` queries at all.** `.sector-grid` is a fixed 3 columns and `.hist-cols` has fixed widths, so it squeezes badly under ~700 px.
- More importantly, the server binds **127.0.0.1 only** and the Host allow-list rejects LAN hosts (`dashboard.py:114, 203`). **A tablet or phone can't open the dashboard at all.** For a sim-racing companion, the phone or tablet on the wheel stand is the main second screen.

**Component / render structure.**
- One `render()` rebuilds `innerHTML` for the sector boxes, history and trend every 200 ms. It's cheap (0.82 ms, per `PERF.md`), but it destroys hover state, so the trend-bar `title` tooltips flicker away, and no DOM is ever stable for assistive tech.
- Keying updates, or only re-rendering when `laps.length`/`currentSector` changes, fixes both.
- State management is a single global `state`, which is fine at this size.

**Accessibility.**
- `--faint #4E5666` on `--bg #0A0C10` is **≈2.7:1** (fails WCAG AA). It's used for 11–13 px labels (`dashboard.html:89, 98, 103, 111, 116`). Raise it to about `#6B7385` (≥4.5:1).
- The pace colours (purple/green/yellow) follow F1 convention, and a signed delta is always shown next to them, which is good. The wear bar is **red/yellow/green only**, and deuteranopes can't tell yellow from green. Add threshold ticks or text such as "HIGH".
- Tyre colours carry the compound name as text, so they're OK.
- Modals have no Esc-to-close or focus trap, and there are no `aria-live` regions. A polite live region mirroring the spoken radio lines would also help hard-of-hearing users.

**Theming / polish.**
- Google Fonts and supabase-js are pulled from the internet at runtime. Offline at a LAN party, the fonts fall back and **login breaks**.
- The title and default label still say "F1 25" (`dashboard.html:6`, `dashboard.py:43`).
- Onboarding tells every user to set **UDP Format 2025** (`dashboard.html:1317, 1375`). **F1 22–24 can't select 2025**, and the codec auto-detects anyway. Say "any format".

**Features comparable apps offer that PitWall lacks.** Most of the data is already arriving.

| Feature | Data already received? |
|---|---|
| Per-corner tyre wear (currently averaged) + surface/inner temps + pressures | ✅ Car Damage / Car Telemetry |
| ERS store / deploy mode / harvested | ✅ Car Status (decoded, unused) |
| Fuel delta (target vs. actual per lap, "save X kg") | ✅ Car Status |
| Gaps ahead/behind, positions, driver names | Lap Data (all 22 cars) + Participants |
| Pit window / strategy / tyre sets available | Session + Tyre Sets |
| Weather now + forecast | Session (forecast samples) |
| Car damage (wings, floor, gearbox, engine wear) | ✅ Car Damage |
| **Lap comparison overlay** (speed/brake vs. best, per distance) | ✅ already in `trace.LapTrace`; just not drawn |
| Session history / export (CSV) | ✅ local |
| Warnings / track-limit counts, penalties outstanding | ✅ Lap Data |
| Radio log (transcript of spoken lines) | ✅ trivial |

---

## Step 5 — Product & security

**Onboarding.**
- The 3-step guide with the real LAN IP is good.
- Gaps:
  - Wrong format advice (above).
  - No **"packets arriving from X.X.X.X, format N, last N s ago"** diagnostic panel.
  - No port-in-use or firewall-blocked detection.
  - No PC-same-machine hint (`127.0.0.1`).
  - Parse-skip warnings are invisible (H-8).
- The **hard paywall hides the connection check**. `Engineer.handle` drops every packet before `dash.touch()` when unentitled (`main.py:140-141`), so a prospective buyer can't confirm their console connects before paying. They see only "NO SIGNAL".
- The macOS build is unsigned and un-notarised, so testers get Gatekeeper warnings.

**Supabase auth / RLS.**
- `profiles` has RLS on, select/update are restricted to own rows, and column-level `grant update (display_name)` blocks self-promotion to `active`. **Correct.**
- `handle_new_user` is `security definer` with `search_path` pinned. Good.
- `laps` insert policy only checks `user_id`, not that `session_id` belongs to the caller. It's unused today, so fix it before any sync ships.

**Secrets.**
- `.env` holds only the URL and anon key, is gitignored, and CI injects them from secrets. OK.
- The refresh token is plaintext in `auth.json` at 0600. Acceptable. Keychain / Credential Manager would be better.
- Aside, outside this repo: `~/Documents/Claude/github-recovery-codes.txt` sits in the same workspace as all these projects. Move it into a password manager.

**Input validation / rate limiting.**
- The local server's Host/Origin checks are the right defence, and the log shows them rejecting `evil.example`.
- `/auth/session` reads an unbounded `Content-Length`. It's local-only, but cap it.
- Supabase's built-in auth rate limits apply. No edge-function rate limiting.
- `create-checkout` echoes `String(e)` to the client.

**Stripe design.**
- One tier, no trial, no free tier, **no Customer Portal**. See the findings below.

---

## Step 6 — Ranked findings

### Critical

**C-1. Invalid (deleted) laps count as "best" on the dashboard, in recaps and in the long-term DB.**
- **Problem:**
  - `bestSectors` and `bestLapT` use every lap, including `invalid: true` (`dashboard.html:669-671, 726`).
  - `analysis.session_recap` does the same (`analysis.py:43-45`).
  - `driver_db` archives that recap (`main.py:592-598`).
  - Backfilled laps drop the valid flags entirely (`main.py:632-634`).
- **Result:** the voice says "lap deleted, track limits" while the dashboard paints it purple as BEST. Then next session: "Last time here your best was…" quotes a deleted lap.
- **Evidence:** `logs/pitwall.log`: "best S2 mismatch: game says 42.800, we tracked 42.600". The game excludes the invalid sector and we didn't.
- **Fix:** carry `invalid` on every lap entry, including backfill from `valid_flags`. Filter valid laps for best, theoretical, recap and DB, falling back to invalid only when no valid lap exists. Mirror the `Analyzer` rule. Mark deleted rows in history.
- **Effort:** S

**C-2. Priority safety calls can be delayed by seconds or dropped entirely (TTS queue).**
- **Problem 1:** `say(priority=True)` flushes the queue and kills the *current playback process* (`speak.py:58-79`). If the worker is *synthesising* at that moment, there's no process to kill. The routine line finishes synthesis, then **plays in full before the flag call**.
- **Problem 2:** while that happens, two routine lines arriving fill the maxsize-2 queue, and the overflow path drops the **oldest** item (`speak.py:66-74`), which is the priority line.
- **Evidence:** "Yellow flag ahead" can be lost behind "You lost three tenths…". The log shows 11 backlog drops in one session, so the queue does fill.
- **Fix:**
  - A two-lane queue: priority lines are never dropped by routine lines.
  - An interrupt `Event` / generation counter that the worker checks before every `Popen` and after every synthesis.
  - Drop, don't play, any routine line synthesised under a stale generation.
- **Effort:** S–M

**C-3. Spectator / player index 255 throws on every per-car packet.**
- **Problem:** `parse_lap_data(data, header.player_car_index)` and friends (`main.py:223, 249, 296, 304, 307`) index past the buffer when the index is 255. The result is hundreds of logged tracebacks per minute (throttled after 50), a mute app, and a misleading "NO SIGNAL"-style experience.
- **Fix:** if `player_car_index >= 22`, use `Session.m_spectatorCarIndex` when `isSpectating`, otherwise skip per-car packets and show "SPECTATING" on the dashboard.
- **Effort:** S

### High

**H-1. No self-serve cancellation (Stripe Customer Portal missing).**
- **Problem:** there's no manage/cancel path anywhere in the app or the functions. For a consumer subscription that's a legal and chargeback risk (AU/EU/US-CA "easy cancel" rules) and a support burden.
- **Fix:** a `create-portal-session` edge function plus a MANAGE SUBSCRIPTION button next to LOGOUT.
- **Effort:** S

**H-2. The webhook can silently lose, or wrongly apply, status changes.**
- **Problem:**
  - `admin.from(...).update()` returns `{error}` and doesn't throw. Errors are ignored, a 200 goes back, and Stripe never retries (`stripe-webhook/index.ts:42-57`).
  - Events are processed in arrival order, but Stripe doesn't guarantee ordering. A late `subscription.updated(incomplete)` maps to `canceled` after `checkout.session.completed` set `active`.
  - `subscription.*` events arriving before `checkout.session.completed` has stored `stripe_customer_id` match 0 rows.
- **Fix:**
  - Check `error` and return 500.
  - On every event, re-fetch the subscription from Stripe and write its *current* status plus `current_period_end`, which makes it idempotent and order-independent.
  - Store `stripe_subscription_id`.
  - Log `event.id` for idempotency.
- **Effort:** M

**H-3. Duplicate subscriptions and duplicate customers.**
- **Problem:** `create-checkout` passes `customer_email` every time, so a new Stripe Customer is created per attempt. Nothing stops an already-`active` user from subscribing again (`create-checkout/index.ts:34-41`).
- **Fix:** reuse `profiles.stripe_customer_id` (create it once), and refuse or redirect to the portal when already active.
- **Effort:** S

**H-4. The offline license grace is trivially forgeable.**
- **Problem:** `license.json` is unsigned JSON. Write `status: active` with a future `checked_at`, put any string in `auth.json`, and block the Supabase host. `grace_verdict` then returns entitled indefinitely, because negative elapsed time gives more than 7 days left (`cloud.py:47-53, 118-122`).
- **Fix:**
  - Clamp `checked_at <= now`.
  - Have the server issue a signed licence (edge function returns an Ed25519-signed `{user_id, status, exp}`; the app verifies it with an embedded public key).
  - Accept that a determined user can patch a PyInstaller build. The goal is to stop the casual forge.
- **Effort:** M

**H-5. Laps crossing the line in the pit lane are lost.**
- **Problem:** `state.py:111-113` resyncs whenever `pit_status != 0`, so a lap-number increment inside the pit lane never emits `LapComplete`.
- **Result:** in races with a stop, the in-lap disappears from history, race lap counts drift, and the stint window is skewed.
- **Fix:** allow lap completion while pitting and tag the lap `pit: true`. Exclude it from the stint median and from coaching.
- **Effort:** S

**H-6. The dashboard can't be used on a phone or tablet.**
- **Problem:** 127.0.0.1-only bind, a localhost Host allow-list, and no responsive layout (`dashboard.py:114, 203`; no `@media` in `dashboard.html`).
- **Fix:**
  - An opt-in "Allow other devices" toggle that binds `0.0.0.0`.
  - A random pairing token in a QR code, sent as a cookie or bearer and required on every request.
  - Keep `/auth/*` localhost-only.
  - Add 2 breakpoints: phone (one-column, Δ hero) and tablet (2-column).
- **Effort:** M

**H-7. The hard paywall prevents setup verification; there's no trial.**
- **Problem:** unentitled users can't see LIVE, so they can't confirm the console connects before paying (`main.py:140-141`).
- **Fix:**
  - Always run `dash.touch()` and the connection diagnostics.
  - Gate voice and coaching only, or add `subscription_data.trial_period_days: 7` in checkout.
  - Treat `past_due` as entitled while Stripe retries (`cloud.py:39`).
- **Effort:** S

**H-8. Parser failures and unsupported formats are invisible to the user.**
- **Problem:** a size mismatch skips the packet type with one log line (`main.py:174-183`), and an unsupported format logs once (`main.py:150-156`). In both cases the dashboard just says NO SIGNAL or goes quiet.
- **Fix:** expose `diagnostics` in `/state.json`: source IP, format, per-packet-ID counts, skipped IDs with expected vs. actual size, last packet age. Render them in HELP as a "Connection" panel with plain-language advice.
- **Effort:** S

**H-9. supabase-js is loaded from `esm.sh@2` at runtime, unpinned and with no SRI, in the page that handles credentials.**
- **Problem:** a CDN compromise or a breaking 2.x release hijacks or breaks login. Being offline also breaks login (`dashboard.html:1159`).
- **Fix:** vendor a pinned `supabase.min.js` into the bundle and serve it from the local server. Better still, drop supabase-js: the Python side can call `/auth/v1/token?grant_type=password` directly, so the page never touches tokens.
- **Effort:** S

### Medium

| # | Problem | Evidence | Fix | Effort |
|---|---|---|---|---|
| M-1 | Onboarding tells F1 22–24 users to pick format "2025" | `dashboard.html:1317, 1375` | "Any format works". Show the detected game in the badge | S |
| M-2 | Track IDs 1, 28, 39–41 missing (Paul Ricard, Portimão, F1 25 reverse layouts) | `packets.py:148-154`, `benchmarks.json`, `trace.py` TRACK_CORNERS | Add names, benchmarks, and turn tables (reversed distances for 39–41) | S |
| M-3 | `driver_db` and targets ignore game year, formula and weather | `driver_db.py:56`, `reference.py:58-60` | Key by `(track_id, formula, game_year)`. Skip targets in wet or non-F1 formula | S |
| M-4 | F1 22 red flags never called (no RDFL event in 22) | `main.py:475` | Infer from Session: all marshal zones red, or session-state change | S |
| M-5 | Tyre wear averaged over 4 corners; hottest/most-worn corner hidden | `main.py:260, 297` | Send all 4 and show a 2×2 tyre widget | S |
| M-6 | Port in use crashes with a raw traceback | `main.py:750`, `dashboard.py:203`, log 2026-08-30 17:00:51 | Catch `OSError` with a friendly message: "PitWall is already running". Optionally focus the existing tab | S |
| M-7 | `/recap` touches engine state from the HTTP thread | `dashboard.py:165-171`, `main.py:433` | Enqueue it and let the UDP loop run it | S |
| M-8 | Session lost on crash | `main.py:588, 819` | Checkpoint the current session each lap and archive the checkpoint on next start | S |
| M-9 | `pytest` from repo root errors; tests not in CI; deps unpinned | run output; `requirements.txt` | `pytest.ini` with `testpaths = tests`; `requirements-dev.txt`; a CI job on push (macOS + Windows) | S |
| M-10 | Contrast of `--faint` ≈2.7:1; colour-only wear bar; no Esc/focus trap; no live region | `dashboard.html:12, 89-116` | Token bump, text thresholds, modal a11y, `aria-live` radio log | S |
| M-11 | Glanceability: clock is the hero, Δ is small; "BACK OFF" label | `dashboard.html:52, 713` | Racing layout: Δ hero plus a colour band. Rename to OFF PACE | M |
| M-12 | macOS build unsigned and un-notarised; 390 MB zip | `PitWall.spec` `codesign_identity=None`; `PitWall-beta.zip` | Developer ID sign + notarise in CI. Switch to the int8 Kokoro model (~88 MB vs ~310 MB), subject to a listening test | M |
| M-13 | `Codec2022.SIZES[3]` is 36 but real F1 22 events are 40 | `formats.py:337`, log | Set 40, keep the floor | S |

### Nice-to-have

- **Lap comparison overlay:** draw the speed/brake trace vs. the best lap from the `LapTrace` data already held (M).
- **ERS / fuel-delta / damage / weather panels** from already-decoded packets (M).
- **Race intel:** all-car Lap Data plus Participants for gaps ahead/behind with names, and undercut/pit window from Tyre Sets (L).
- **SSE** instead of 5 Hz polling (S).
- **Real-capture regression fixtures:** record 30 s of each game with `--raw` to a `.bin` and replay it in tests (S once captures exist).
- **Keychain storage** for the refresh token (S).
- **Opt-in cloud sync** of `driver_db` using the existing `sessions`/`laps` tables, after fixing the `laps` insert policy (L).
- Fix the `packets.py` doc comment about when the speed-trap fields arrived (S).

---

## TTS — how to improve it

What's there is good: a bundled neural voice, sentence streaming (1.9 s → 0.9 s to first word), pre-rendered flag calls, and a `say`/SAPI fallback. The remaining problems, in priority order:

1. **Fix the priority path (C-2).** Use two lanes (safety / routine) and never drop safety for routine. Add an interrupt generation that the worker checks before playing *and* after synthesising, so a flag call cuts in within one process kill rather than one synthesis plus one full line.

2. **Stop talking so much, and never with stale news.** The log shows 11 backlog drops in one session, which means the engineer is overrunning the driver. Add:
   - **TTL per line:** drop a routine line not started within ~3 s. A sector-1 call heard in sector 3 is noise.
   - **Coalescing:** a live-corner call and the sector call that names the same corner become one line. The code already partly dedupes this via `_zone_mentions`. Do it at the queue.
   - **Per-sector talk budget:** at most one routine line per sector. Everything else goes to the dashboard radio log.
   - **User verbosity setting:** Quiet (flags + lap time only) / Normal / Coach.

3. **Don't talk in braking zones.** Real engineers wait for a straight. Gate the *start* of routine lines on driving state: throttle ≥ 0.95, brake = 0, |steer| < 0.1. Alternatively, derive "straight ahead for ≥ N m" from the braking zones already detected in `trace.py`. Defer up to the TTL and drop otherwise. This is the single biggest perceived-quality upgrade.

4. **Play audio in-process instead of temp WAV + `afplay`/PowerShell per chunk.**
   - Every chunk currently spawns a process: about 50–150 ms on macOS and **300–600 ms for PowerShell `SoundPlayer` on Windows**, plus an audible gap at each sentence seam.
   - Use `sounddevice` (PortAudio) to stream the float32 samples straight from Kokoro. `kokoro-onnx` also has `create_stream()`, which yields audio per chunk.
   - That gives gapless playback, instant interrupt (`stream.abort()`), no temp files, and a chosen **output device** (headset vs. speakers), which is a common request when the console audio goes to a headset.

5. **Radio character and intelligibility.** An optional "team radio" filter makes speech cut through engine noise and sells the persona:
   - Band-pass about 300–3,400 Hz.
   - Light compression.
   - A 40 ms squelch click in and out.
   It's a few lines of numpy on the sample buffer before playback, with a toggle.

6. **A text-normalisation pass before synthesis**, with unit tests on the spoken form:
   - `P4` → "P four".
   - `kph` → "k" (as a real engineer says it).
   - Avoid "the braking zone 4.9 k in" (heard in the log). Say "the braking zone after the long straight" or auto-number corners from the recorded track map's curvature, so every track gets "turn N" instead of kilometres.
   - "let him through" → "let the car through" (`brain.py:110`).

7. **Thread safety and warm-up contention.**
   - `_get_kokoro()` has no lock, so the warm-up thread and the worker can build two models concurrently (`speak.py:197-201`). Add a lock.
   - Pre-rendering 11 calls takes ~7 s of CPU, competing with the first real lines. Run it at lower priority, or pause pre-cache while the worker is busy.
   - Extend the cache into a small **LRU of recently synthesised lines**. "That's your best second sector so far." and the radio checks repeat verbatim.

8. **Fallback gaps.** The `say` retry path (`speak.py:167`) isn't registered, so it can't be interrupted by a flag call. Route it through `_register` too.

9. **Controls in the dashboard:** voice picker (bm_lewis / bm_george / bm_daniel), speed, volume, mute hotkey, "repeat last call", and a scrolling **radio log** of every line spoken. The log also doubles as the hard-of-hearing accessibility feature.

10. **Size and speed:** test `kokoro-v1.0.int8.onnx`. It's roughly a quarter of the download and usually faster on CPU. Do a blind listening check before switching.

---

## Roadmap

**Phase 0 — Correctness and safety (≈1 week)**
C-1 invalid laps · C-2 TTS priority lanes · C-3 spectator guard · H-5 pit-lane laps · M-6 port-in-use · M-7 recap threading · M-9 pytest config + CI · M-13 F1 22 event size.

**Phase 1 — Billing you can launch with (≈1 week)**
H-1 Customer Portal · H-2 order-independent, error-checked webhook · H-3 customer reuse / no double-subscribe · H-4 signed licence + clock clamp · H-7 free connection check + trial + past_due grace · H-9 vendored auth JS.

**Phase 2 — Onboarding and multi-game polish (≈1–2 weeks)**
H-8 diagnostics panel · M-1 format-agnostic onboarding · M-2 missing track IDs · M-3 DB keyed by game/formula · M-4 F1 22 red flag · M-12 sign/notarise + smaller model · real-capture fixtures for F1 22/23/24.

**Phase 3 — The voice becomes the product (≈2 weeks)**
TTS items 2–6 and 9: TTL/coalescing/verbosity, don't-talk-in-corners, in-process streaming audio with device choice, radio FX, text normalisation, auto-numbered corners, radio log.

**Phase 4 — Second screen (≈2–3 weeks)**
H-6 LAN mode with pairing + responsive layouts · M-11 racing layout · M-10 a11y · M-5 4-corner tyres · ERS/fuel-delta/damage/weather panels · lap-comparison overlay · SSE.

**Phase 5 — Race intelligence (later)**
All-car Lap Data + Participants for gaps and names; Tyre Sets for pit window/undercut; Final Classification into the debrief; optional cloud sync of history.
