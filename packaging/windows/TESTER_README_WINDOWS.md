# PitWall — F1 25 Voice Race Engineer (beta test build, Windows)

Thanks for testing this. It listens to F1 25's live telemetry, gives you
spoken race-engineer coaching, and shows a live pit-wall dashboard in
your browser. This is early/unsigned software, so Windows will ask you
to confirm a couple of things on first launch — that's expected, see
below.

## Requirements

- **Windows 10 (version 1803 / April 2018 or newer) or Windows 11.**
  Older Windows won't work — it needs the built-in `curl`.
- F1 25 on Xbox or PC, same Wi-Fi/LAN network as this PC.

## Running it

1. Double-click **`PitWall.bat`**. A console window opens and starts the
   app — leave that window open while you're driving; closing it stops
   the app.
2. **Windows will block it the first time** with a blue *"Windows
   protected your PC"* (SmartScreen) box. This is normal for software
   distributed outside the Microsoft Store. To allow it:
   - Click **More info** → **Run anyway**. (Only needed once.)
3. **Windows Defender Firewall will ask** whether to allow PitWall to
   receive network connections — tick **Private networks** and click
   **Allow access**. This is the game's telemetry reaching the app;
   nothing else uses the network. (Only needed once.)
4. Your browser should open automatically to the dashboard
   (`http://127.0.0.1:8777`). If it doesn't, open that address
   yourself.

## Sign up (this is a test account — no real charge)

The app is gated behind a $4.99/month subscription, but **billing is
in Stripe test mode right now** — nothing is actually charged. Sign up
with any email/password, then on the "Subscribe" screen use Stripe's
official test card:

```
Card number: 4242 4242 4242 4242
Expiry:      any future date (e.g. 12/30)
CVC:         any 3 digits
Name/Zip:    anything
```

That's it — no real payment method needed, and no real money moves.

## Telemetry setup

F1 25 → **Settings → Telemetry Settings**:

| Setting | Value |
|---|---|
| UDP Telemetry | On |
| UDP Broadcast Mode | Off |
| UDP IP Address | this PC's LAN IP (run `ipconfig`, use the IPv4 address) |
| UDP Port | 20777 |
| UDP Format | **2025** |

If F1 25 runs on the **same PC** as PitWall, you can set the UDP IP
Address to `127.0.0.1` instead.

Get on track — the dashboard flips from "NO SIGNAL" to "LIVE" and the
voice starts calling your sectors and laps.

## Quitting

Close the console window, or click into it and press **Ctrl+C**.

## Known limitations (it's early beta)

- First launch is slow-ish (a few seconds) while the voice model
  warms up — normal.
- Track maps and named corners are best-effort stylized approximations
  right now — if a corner gets named wrong or a map looks off, that's
  useful feedback, tell Jordan which track.

## If something breaks

The app writes a log file. When you launch it, the console prints its
location — something like:

```
Logs (send this if something breaks): ...\PitWall\logs\pitwall.log
```

If anything crashes or misbehaves, **send Jordan that `pitwall.log`
file** — it records what happened right up to the problem, which is far
more useful than a screenshot. (The app also tries to keep running
through minor glitches rather than quitting on you.)

## Feedback

Tell Jordan anything that's confusing, broken, or just feels off —
that's exactly what this test round is for.
