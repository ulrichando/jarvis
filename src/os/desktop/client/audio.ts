// Mic capture + speaker playback via parecord/paplay (PulseAudio/pipewire).
// These shell out to system tools rather than using a native library — simpler and
// Bun's child_process support is fine for this.

export type RecordOpts = {
  /** Max record duration in seconds. Recording stops when the timer fires or the caller ends the subprocess. */
  maxSeconds?: number;
  /** Format. Default: wav at 16kHz mono — matches what Groq Whisper wants. */
  rate?: number;
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

  // parecord -d @DEFAULT_SOURCE@ --format=s16le --rate=... --channels=1 --file-format=wav -
  const args = [
    "parecord",
    "--format=s16le",
    `--rate=${rate}`,
    "--channels=1",
    "--file-format=wav",
    "-", // stdout
  ];
  const proc = spawner(args);

  let stopped = false;
  const stop = () => {
    if (stopped) return;
    stopped = true;
    try { proc.kill("SIGTERM"); } catch { /* ignore */ }
  };

  const timer = setTimeout(stop, maxSec * 1000);

  const done = (async () => {
    try {
      const bytes = new Uint8Array(await new Response(proc.stdout as ReadableStream).arrayBuffer());
      await proc.exited;
      return bytes;
    } finally {
      clearTimeout(timer);
    }
  })();

  return { done, stop };
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
