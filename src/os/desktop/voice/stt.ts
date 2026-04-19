// Speech-to-text via Groq Whisper.

export type STTOpts = {
  apiKey: string;
  audio: Uint8Array | ArrayBuffer;
  filename?: string;
  model?: string;
  language?: string;
  /** For tests: override fetch. */
  fetchFn?: typeof fetch;
};

const DEFAULT_MODEL = "whisper-large-v3";

export async function transcribe(opts: STTOpts): Promise<string> {
  const f = opts.fetchFn ?? fetch;
  const form = new FormData();
  const blob = new Blob([opts.audio]);
  form.append("file", blob, opts.filename ?? "input.wav");
  form.append("model", opts.model ?? DEFAULT_MODEL);
  if (opts.language) form.append("language", opts.language);

  const resp = await f("https://api.groq.com/openai/v1/audio/transcriptions", {
    method: "POST",
    headers: {
      authorization: `Bearer ${opts.apiKey}`,
    },
    body: form,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`STT failed (${resp.status}): ${text.slice(0, 500)}`);
  }
  const body = (await resp.json()) as { text?: string };
  if (typeof body.text !== "string") {
    throw new Error(`STT response missing 'text' field: ${JSON.stringify(body).slice(0, 200)}`);
  }
  return body.text;
}
