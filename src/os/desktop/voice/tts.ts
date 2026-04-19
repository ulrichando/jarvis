// Text-to-speech via Groq Orpheus (Groq's OpenAI-compatible audio endpoint).

export type TTSOpts = {
  apiKey: string;
  text: string;
  voice?: string;
  model?: string;
  format?: "wav" | "mp3" | "flac" | "ogg";
  /** For tests: override fetch. */
  fetchFn?: typeof fetch;
};

const DEFAULT_MODEL = "canopylabs/orpheus-v1-english";
const DEFAULT_VOICE = "daniel";

export async function synthesize(opts: TTSOpts): Promise<Uint8Array> {
  const f = opts.fetchFn ?? fetch;
  const resp = await f("https://api.groq.com/openai/v1/audio/speech", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${opts.apiKey}`,
    },
    body: JSON.stringify({
      model: opts.model ?? DEFAULT_MODEL,
      voice: opts.voice ?? DEFAULT_VOICE,
      input: opts.text,
      response_format: opts.format ?? "wav",
    }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`TTS failed (${resp.status}): ${text.slice(0, 500)}`);
  }
  const buf = await resp.arrayBuffer();
  return new Uint8Array(buf);
}
