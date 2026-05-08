export const DESCRIPTION =
  'Converts text to speech using the system TTS engine (espeak-ng). Speaks text aloud through the speakers.'

export const PROMPT = `Use this tool to speak text aloud to the user through the system speakers.

- Uses \`espeak-ng\` for synthesis, piped to PipeWire/PulseAudio/ALSA for playback.
- Keep the text concise — long passages are hard to follow aurally.
- Maximum ~1000 characters per call.
- Optionally set \`voice\` to "male" (default) or "female" for different vocal tones.
- Optionally set \`speed\` in words-per-minute (80–450, default ~160).
- This is system-level TTS — it runs independently of the jarvis-voice-agent service.
- Use sparingly: prefacing a spoken summary is fine; reading entire file contents aloud is not.

## When to use this tool

- The user explicitly asks you to "say something" or "speak"
- Delivering a short spoken confirmation or alert
- User is likely in a hands-free or drive-mode scenario

## When NOT to use

- The user is typing and hasn't asked for speech output
- Long text / code listings — the user can read
- Every response — voice is push-to-talk on input but output should be on-demand only
`
