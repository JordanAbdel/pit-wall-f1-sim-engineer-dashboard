"""Optional conversational brain for questions the engineer has no script for.

brain.RaceEngineer.answer covers the questions a driver actually asks on
the radio (gap, tyres, fuel, pace, where the time is going) straight from
the data, instantly and offline. Anything else — "should I pit?", "what's
the plan for the last stint?", "how do I get through Maggotts faster?" —
can go to Claude, with the live session as JSON, so the engineer can still
answer in his own voice.

It is off unless an Anthropic API key is configured (ANTHROPIC_API_KEY in
the environment or .env). Without one, unscripted questions get "say
again?" exactly as before; nothing here is ever on the path of a coaching
or safety call.
"""
import json
import logging
import os
import threading

log = logging.getLogger("ai")

MODEL = "claude-opus-5-5"
TIMEOUT_S = 12.0
HISTORY_TURNS = 6  # recent radio exchanges kept for follow-up questions

SYSTEM = """You are the race engineer on a Formula 1 driver's team radio, \
in the F1 video game. You are British, calm, warm and precise — think of a \
real pit-wall engineer who knows this driver well.

Your reply is spoken aloud over the radio while the driver is at speed:
- One or two short sentences. Never more.
- Plain spoken English: no markdown, no lists, no emoji, no abbreviations \
like "kph" or "S1" — say "kilometres an hour", "the first sector".
- Say lap times the way an engineer would: "a one twenty-eight point four".
- Use only the facts in the session JSON you are given. If the answer is \
not in it, say you don't have that on your screens — never invent data.
- Give one clear instruction when the driver asks what to do.
- If the question is not about driving or the session, answer briefly and \
steer back to the job."""


class EngineerAI:
    def __init__(self):
        self._client = None
        self._history = []
        self._lock = threading.Lock()
        self.enabled = False
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return
        try:
            import anthropic
        except ImportError:
            log.info("ANTHROPIC_API_KEY is set but the anthropic package is not "
                     "installed — free-form questions are off")
            return
        self._anthropic = anthropic
        self._client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=1)
        self.enabled = True
        log.info("free-form radio questions on (%s)", MODEL)

    def ask(self, question: str, ctx: dict) -> str | None:
        """The engineer's spoken reply, or None if Claude is unavailable,
        declined, or took too long — the caller falls back to "say again"."""
        if not self.enabled:
            return None
        anthropic = self._anthropic
        session = json.dumps(ctx, sort_keys=True, default=str)
        with self._lock:
            history = list(self._history)
        messages = history + [{
            "role": "user",
            "content": f"<session>{session}</session>\n\nDriver: {question}",
        }]
        try:
            response = self._client.beta.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=SYSTEM,
                output_config={"effort": "low"},  # a radio reply, not an essay
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=messages,
            )
        except anthropic.APIConnectionError:
            log.warning("free-form question failed: no connection")
            return None
        except anthropic.RateLimitError:
            log.warning("free-form question failed: rate limited")
            return None
        except anthropic.APIStatusError as e:
            log.warning("free-form question failed: HTTP %s %s", e.status_code,
                        e.message)
            return None
        if response.stop_reason == "refusal":
            return None
        text = " ".join(b.text for b in response.content
                        if b.type == "text").strip()
        if not text:
            return None
        with self._lock:
            # Keep the plain question (not the JSON) so history stays small.
            self._history = (self._history + [
                {"role": "user", "content": f"Driver: {question}"},
                {"role": "assistant", "content": text},
            ])[-2 * HISTORY_TURNS:]
        return text
