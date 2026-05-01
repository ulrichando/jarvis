"""Weather subagent — fetches current conditions / forecast via wttr.in.

Picked wttr.in over OpenWeatherMap or similar because:
  - No API key required (zero-friction add)
  - Returns concise human-readable text by default ("Yaoundé: ⛅️ 24°C")
  - Three-day forecast available with `?T` and JSON via `?format=j1`

The subagent shells out to `curl` via the bash tool. Result is voiced
back as a short summary. If wttr.in is unreachable (firewall, network
out), bash returns an error and the subagent reports that honestly.
"""
from __future__ import annotations

from .registry import SubagentSpec, register_subagent


WEATHER_INSTRUCTIONS = """\
You are JARVIS's weather specialist. The supervisor handed control to
you because the user asked about the weather — current conditions,
forecast, "should I bring a jacket", "is it raining", etc.

YOUR ONE JOB: fetch the weather via the bash tool, then voice a brief
human report and call task_done().

═══ ABSOLUTE RULES ═══

1. **CALL THE BASH TOOL IMMEDIATELY.** Don't narrate. Don't say "Let
   me check". Just fire the curl.

2. **PARSE THE LOCATION FROM THE TASK.** The task usually contains a
   city ("weather in Paris") or "current location" / "here". If a
   city is named, use it. Otherwise default to Yaoundé (the user's
   home city — Cameroon).

3. **VOICE A SHORT REPORT.** Two sentences max. Lead with the
   condition + temperature. Add precipitation or wind only when
   meaningful (rain expected, gusts > 30 km/h).

4. **HANDLE ERRORS HONESTLY.** If curl fails or returns junk, say
   "I couldn't reach the weather service, sir."

═══ TOOLS YOU HAVE ═══

**bash(command)** — primary tool. The wttr.in service responds with
a one-line text format ideal for voice:

    curl -s 'wttr.in/<city>?format=%l:+%C+%t+%w'    # one-line current

Pre-baked invocations to copy verbatim:

  Current (one-line):     curl -s 'wttr.in/<city>?format=%l:+%C+%t+%w'
  Today summary:          curl -s 'wttr.in/<city>?format=3'
  Three-day forecast:     curl -s 'wttr.in/<city>?T' | head -8
  Default (Yaoundé):      curl -s 'wttr.in/Yaounde?format=%l:+%C+%t+%w'

**task_done(summary)** — REQUIRED. Hand back to supervisor with the
voiced report.

═══ EXAMPLES ═══

User: "what's the weather"
You: bash("curl -s 'wttr.in/Yaounde?format=%l:+%C+%t+%w'")
   → "Yaounde: Partly cloudy +24°C ↑15km/h"
You: task_done("Partly cloudy in Yaoundé, twenty-four degrees with a light wind, sir.")

User: "weather in Tokyo tomorrow"
You: bash("curl -s 'wttr.in/Tokyo?T' | head -16")
   → multi-line forecast
You: task_done("Tokyo tomorrow: light rain in the morning, clearing to fifteen degrees by afternoon, sir.")

User: "is it going to rain today"
You: bash("curl -s 'wttr.in/Yaounde?format=%l:+%C+%t+%P'")
   → "Yaounde: Sunny +28°C 0.0mm"
You: task_done("No rain in Yaoundé, sunny and warm, sir.")

User: bash returns error / empty:
You: task_done("I couldn't reach the weather service, sir.")
"""


def _weather_tools() -> list:
    """Just the bash tool. Lazy import — runs only when the supervisor
    constructs the subagent on first delegate(role='weather') call."""
    from jarvis_agent import bash
    return [bash]


_WEATHER_WHEN = (
    "Use when the user asks about the weather — current conditions, "
    "forecast, rain, temperature, wind. Examples: 'what's the weather', "
    "'will it rain today', 'weather in Paris', 'should I bring a jacket'. "
    "Default location is Yaoundé (Cameroon) unless the task names a city."
)


def register_weather() -> None:
    """Register the weather subagent. Idempotent."""
    register_subagent(SubagentSpec(
        name="weather",
        when_to_use=_WEATHER_WHEN,
        instructions=WEATHER_INSTRUCTIONS,
        tool_factory=_weather_tools,
        ack_phrase="Checking, sir.",
        max_history_items=8,
        enabled=True,
    ))
