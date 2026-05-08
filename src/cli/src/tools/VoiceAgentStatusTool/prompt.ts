export const DESCRIPTION =
  'Check the status and health of the jarvis-voice-agent service (LiveKit Python worker).'

export const PROMPT = `Use this tool to check whether the JARVIS voice agent is running and healthy.

The voice agent (\`jarvis-voice-agent.service\`) is a LiveKit Python worker that handles:
- Real-time speech-to-text (STT) via Groq Whisper
- LLM conversation via Groq Llama
- Text-to-speech (TTS) via Groq Orpheus + Edge-TTS fallback
- Specialist handoffs (desktop, browser, browser_v2)

## What this tool returns

- Service status: running / stopped / failed
- Uptime (if running)
- PID and memory usage
- Last 20 log lines from the voice-agent log (if \`includeLogs\` is true)

## When to use

- User reports voice is not responding ("Jarvis can't hear me")
- After a voice-agent restart, to verify it came back up
- Troubleshooting: checking if the service crashed or is overloaded
- Before restarting — check if it's already running normally

## When NOT to use

- Proactively / unprompted — the user didn't ask about voice status
- In a loop — the cost is a systemctl call; don't poll
`
