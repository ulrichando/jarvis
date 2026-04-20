// Mic capture via ffmpeg; speaker playback via paplay.
// ffmpeg handles fixed-duration recording (-t) and signal-based stop cleanly,
// whereas parecord returns 0 bytes when SIGTERM'd mid-stream.

export type RecordOpts = {
  /** Max record duration in seconds. Recording stops when the timer fires or the caller ends the subprocess. */
  maxSeconds?: number;
  /** Format. Default: wav at 16kHz mono — matches what Groq Whisper wants. */
  rate?: number;
  /** Fired periodically during recording with the peak amplitude (0..1) of the last window. Enables live HUD pulse. */
  onLevel?: (peak: number) => void;
  /** For tests: override the spawner. */
  spawn?: (cmd: string[]) => Bun.Subprocess;
};

export type RecordHandle = {
  /** Promise that resolves with the captured audio bytes once recording finishes. */
  done: Promise<Uint8Array>;
  /** Stop recording now. Safe to call multiple times. */
  stop(): void;
};

const DEFAULT_MAX_SECONDS = 60;
const DEFAULT_RATE = 16000;

/** Start recording. Returns a handle; call handle.stop() to end recording or wait for the timer. */
export function startRecording(opts: RecordOpts = {}): RecordHandle {
  const rate = opts.rate ?? DEFAULT_RATE;
  const maxSec = opts.maxSeconds ?? DEFAULT_MAX_SECONDS;
  const spawner = opts.spawn ?? ((cmd) => Bun.spawn(cmd, { stdout: "pipe", stderr: "pipe" }));

  // ffmpeg self-terminates at -t; SIGINT for early stop (flushes WAV trailer).
  const args = [
    "ffmpeg",
    "-hide_banner", "-loglevel", "error",
    "-f", "pulse", "-i", "default",
    "-ac", "1",
    "-ar", String(rate),
    "-t", String(maxSec),
    "-f", "wav",
    "pipe:1",
  ];
  const proc = spawner(args);

  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    try { proc.kill("SIGINT"); } catch { /* ignore */ }
  };

  // No JS-side timer — ffmpeg's -t enforces the duration.

  const done = (async () => {
    const chunks: Uint8Array[] = [];
    const WINDOW = Math.max(1600, Math.floor(rate * 0.1) * 2); // ~100ms of s16le mono
    let residual = new Uint8Array(0);
    const onLevel = opts.onLevel;

    const reader = (proc.stdout as ReadableStream<Uint8Array>).getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      chunks.push(value);
      if (onLevel) {
        const merged = new Uint8Array(residual.length + value.length);
        merged.set(residual);
        merged.set(value, residual.length);
        let i = 0;
        while (i + WINDOW <= merged.length) {
          onLevel(peakOf(merged.subarray(i, i + WINDOW)));
          i += WINDOW;
        }
        residual = merged.subarray(i);
      }
    }
    await proc.exited;
    // Concat all chunks into final WAV buffer.
    const total = chunks.reduce((s, c) => s + c.length, 0);
    const out = new Uint8Array(total);
    let off = 0;
    for (const c of chunks) { out.set(c, off); off += c.length; }
    return out;
  })();

  return { done, stop };
}

/** Peak amplitude (0..1) of interpreting bytes as s16le samples. */
function peakOf(bytes: Uint8Array): number {
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let peak = 0;
  for (let i = 0; i + 1 < dv.byteLength; i += 2) {
    const s = Math.abs(dv.getInt16(i, true));
    if (s > peak) peak = s;
  }
  return peak / 32768;
}

export type PlayOpts = {
  /** For tests: override the spawner. */
  spawn?: (cmd: string[], input: Uint8Array) => Bun.Subprocess;
};

/** Play an audio buffer (WAV bytes) through the default sink. Resolves when playback finishes. */
export async function playAudio(bytes: Uint8Array, opts: PlayOpts = {}): Promise<void> {
  const defaultSpawn = (cmd: string[], input: Uint8Array) => {
    const p = Bun.spawn(cmd, { stdin: "pipe", stdout: "inherit", stderr: "inherit" });
    (p.stdin as WritableStreamDefaultWriter<Uint8Array> | { write(b: Uint8Array): void; end(): void }).write(input);
    const maybeEnd = p.stdin as unknown as { end?: () => void };
    maybeEnd.end?.();
    return p;
  };
  const spawner = opts.spawn ?? defaultSpawn;
  const proc = spawner(["paplay", "--"], bytes);
  const exit = await proc.exited;
  if (exit !== 0) throw new Error(`paplay exited with code ${exit}`);
}
