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

/**
 * Strip markdown/code syntax from reply text before feeding TTS.
 * TTS models read backticks, hyphens, slashes as literal characters
 * ("backtick ls dash la backtick") — sanitize so the user hears prose.
 */
export function sanitizeForTTS(text: string): string {
  return text
    // Fenced code blocks: ```lang\n…\n``` — drop entirely, replace with "[code redacted]"
    .replace(/```[\s\S]*?```/g, " ")
    // Inline code: `foo` — drop the backticks, keep the word if short, else drop
    .replace(/`([^`]{1,40})`/g, "$1")
    .replace(/`[^`]{41,}`/g, " ")
    // Markdown bold/italic markers that TTS reads as "asterisk asterisk"
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1")
    .replace(/_([^_]+)_/g, "$1")
    // Headers
    .replace(/^#{1,6}\s+/gm, "")
    // Bullet markers
    .replace(/^\s*[-*+]\s+/gm, "")
    // URLs — read as "a link" instead of character-by-character
    .replace(/https?:\/\/\S+/g, "a link")
    // File paths — same treatment if they're long
    .replace(/\/[\w./-]{20,}/g, "a file path")
    // Shell command patterns commonly left in replies
    .replace(/\$\s*([^\n]+)/g, "")
    // Collapse whitespace
    .replace(/\s+/g, " ")
    .trim();
}

export async function synthesize(opts: TTSOpts): Promise<Uint8Array> {
  const f = opts.fetchFn ?? fetch;
  const cleaned = sanitizeForTTS(opts.text);
  const resp = await f("https://api.groq.com/openai/v1/audio/speech", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${opts.apiKey}`,
    },
    body: JSON.stringify({
      model: opts.model ?? DEFAULT_MODEL,
      voice: opts.voice ?? DEFAULT_VOICE,
      input: cleaned,
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
