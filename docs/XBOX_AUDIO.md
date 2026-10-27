# Hearing your engineer in an Xbox headset

**The problem:** your headset is connected to the Xbox, so you hear the
game, but PitWall's engineer talks through the computer's speakers.

**What doesn't work:** there is no way to install your own app on an Xbox,
and Xbox has no "play audio from my PC" feature. Opening the dashboard in
Edge on the Xbox isn't reliable either: there's no documented guarantee
that a browser tab keeps playing sound while a game is running.

**What does work:** two options. Pick the first one if your headset allows
it, because it needs no software.

---

## Option 1: your headset has Bluetooth too (zero software)

Some Xbox headsets connect to the console and to a Bluetooth device **at
the same time**, and mix the two in your ears:

- **Xbox Wireless Headset** (Microsoft's own, 2021 and later). It pairs to
  the Xbox and a phone or computer simultaneously.
- **SteelSeries Arctis 9X** (Xbox Wireless plus Bluetooth together).
- Many newer headsets advertise "simultaneous Bluetooth" or "dual
  wireless". Check your model's spec page for that exact wording.

If yours does:

1. Put the headset in Bluetooth pairing mode. For the Xbox Wireless Headset,
   hold the power button until the light flashes.
2. On the Mac: **System Settings → Bluetooth**, then connect the headset.
3. Leave PitWall's output on **This computer's default output**
   (dashboard → **Engineer**).

That's it: game audio comes from the Xbox, and the engineer comes over
Bluetooth into the same ears. Bluetooth adds about a fifth of a second of
delay, which is fine for radio calls.

---

## Option 2: Discord bridge (works with any headset, wired or wireless)

Xbox can join Discord voice channels natively, and Discord audio is mixed
into your headset like party chat. So PitWall becomes a second "person" in
a private Discord voice channel, and you join that channel from the Xbox.

```
PitWall ─▶ BlackHole (virtual cable) ─▶ Discord on the Mac (2nd account)
                                               │
                                     Discord voice channel
                                               │
           your headset ◀── Xbox ◀── Discord on Xbox (your account)
```

Everything here is free. One-time setup takes about 15 minutes.

### One-time setup

**1. Install BlackHole** (a free virtual audio cable for Mac). In Terminal:

```bash
brew install --cask blackhole-2ch
```

It asks for your Mac password, because it installs an audio driver. You
can also download it from <https://existential.audio/blackhole/>. After
installing, **BlackHole 2ch** appears as an audio device.

**2. Make a second Discord account** for the engineer, e.g. "PitWall
Radio". Your own account will be on the Xbox; a single account can't be in
the same call twice. Discord allows several accounts, and the desktop app
can switch between them.

**3. Make a private server** (+ in Discord → Create My Own → "Pit Wall")
with a voice channel. Invite the PitWall Radio account.

**4. Link your *own* Discord account to your Xbox:** Discord → User
Settings → **Connections** → Xbox, then follow the prompts. If you linked
them years ago, do it again: voice on console needs the newer permission.

**5. On the Mac, sign Discord in as PitWall Radio**, then go to **User
Settings → Voice & Video**:

| Setting | Value | Why |
|---|---|---|
| Input device | **BlackHole 2ch** | the engineer's voice is the "microphone" |
| Output device | any (or mute) | you don't need to hear the call here |
| Input mode | Voice Activity, sensitivity **manual, low** (around −60 dB) | so the first word isn't clipped |
| Noise suppression | **None** | Krisp treats the radio static as noise and chops the call |
| Echo cancellation | **Off** | there's no echo on a virtual cable |
| Automatic gain control | **Off** | PitWall already levels the voice |

**6. Point PitWall at BlackHole:** dashboard → **Engineer** → *Play the
engineer through* → **BlackHole 2ch** → **Save**. Or start it with:

```bash
build-venv/bin/python main.py --audio-device "BlackHole 2ch"
```

### Every time you race

1. Start PitWall on the Mac, and have Discord (as PitWall Radio) sitting
   in the **Pit Wall** voice channel.
2. On the Xbox, press the **Xbox button** → **Parties & chats** →
   **Discord** → pick **Pit Wall**.
3. On the dashboard, click **Engineer → Radio check**. You should hear him
   in your headset.

### If you can't hear him

- **Nothing at all:** on the Mac, Discord's *Voice & Video → Mic Test*
  should move when you press **Radio check**. If it doesn't, PitWall isn't
  on BlackHole: check the Engineer settings.
- **Mic test moves, but the Xbox is silent:** make sure the Xbox is in the
  same channel, and check **Settings → General → Volume & audio output →
  Chat mixer** (*Do nothing* keeps the game volume up), plus your headset's
  game/chat balance dial.
- **Words cut off at the start:** lower the voice activity sensitivity.
- **Choppy or warbling:** set Noise suppression to **None**.

---

## On Windows

The same bridge works. Use [VB-CABLE](https://vb-audio.com/Cable/)
instead of BlackHole, pick **CABLE Input** in PitWall's Engineer settings,
and set **CABLE Output** as the second Discord account's input. On
Windows, the Xbox app's party chat can replace Discord: run a party from
the PC with a second Microsoft account and invite your gamertag.
