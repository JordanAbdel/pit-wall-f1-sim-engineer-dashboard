# Performance ledger

Measured attempts, kept and rejected alike. Read this before proposing an
optimization — several of the obvious ones were tried and did not pay.

Method: a 14,148-packet corpus captured from `sim.py` (6 laps, Silverstone)
replayed through `Engineer.handle`; browser-side costs measured in-page with
`performance.now()` over 300 iterations, median of 7 runs; TTS measured
against the real kokoro model in `build-venv`, median of 3 runs per line.

## Kept

| Change | Baseline → Result | Why |
|---|---|---|
| Sentence-stream neural TTS (`KokoroTTS._speak`) | time-to-first-word **1947 ms → 875 ms** (−55%) | Kokoro renders a whole utterance before a word can play, so a two-sentence coaching call cost its full synthesis in silence. Chunks now play while the next is generated. The first sentence yields 2.3–2.8 s of audio, comfortably longer than the 0.7–1.2 s needed to build the rest, so playback does not stutter. |
| Pre-render the safety-call vocabulary (`FLAG_CALLS`) | safety-call time-to-first-word **626 ms median / 982 ms worst → 0 ms** | The 11 flag lines are constant strings, and they are the `priority=True` calls where the delay matters most. Sentence streaming cannot help them — each is one short sentence. Synthesis moves to the existing warmup thread (+7.0 s off-thread, while the driver is still in menus); a flag arriving before warmup finishes simply misses the cache and behaves as before. |

## Rejected — measured, not a bottleneck. Do not re-run these.

| Idea | Measurement | Verdict |
|---|---|---|
| Optimize the UDP packet hot path (`Engineer.handle`) | 3.4 µs/packet → **298,000 packets/s**; a live game sends a few hundred | Already ~0.2% of one core. No headroom worth buying. |
| Shrink or delta-encode `/state.json` | 4.3 KB empty, 9.8 KB at 50 laps, 26 KB at 200 laps; serialization 0.013–0.174 ms; 129 KB/s at 5 Hz over loopback at 200 laps | Linear growth, but the absolute numbers are trivial. |
| Cache `dashboard.html` in memory instead of `read_bytes()` per request | 69 KB read, once per page load | Not on any repeating path. |
| Trim the dashboard `render()` at 5 Hz | 0.82 ms steady state (~0.4% of a core); the track-map SVG and weak-turn overlays are already memoized behind `renderedTrackId` / `renderedWeak` | Nothing left to win. |
| Memoize `pointForDistance` (SVG `getTotalLength`/`getPointAtLength`) | 0.188 ms, once per render | 0.09% of a core. Inside noise. |
| Speed up startup / imports | `import main` 58 ms; kokoro model load 500 ms, already off-thread behind `_warmup` | Not user-visible. |

## Note on measuring the dashboard

The in-app browser pane throttles timers and skips layout while hidden —
`setInterval` drops to ~1 Hz and `requestAnimationFrame` stops entirely.
An early pass through it reported `render()` at 23 ms median; measured
synchronously the same function costs 0.82 ms. Measure render cost by
calling it in a loop, not by sampling the poll interval.
