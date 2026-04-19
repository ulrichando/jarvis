// Screen capture via grim. Returns JPEG bytes.
// grim syntax: grim [-o <output>] [-g <region>] -t jpeg [-q 60] -

export type CaptureOpts = {
  monitor?: "focused" | "all" | string;
  quality?: number;
  maxWidth?: number;
  /** For tests: override the command spawner. */
  spawn?: (cmd: string[]) => Bun.Subprocess;
};

export type Capture = {
  jpeg: Uint8Array;
  width?: number;
  height?: number;
};

const DEFAULT_QUALITY = 60;
const DEFAULT_MAX_WIDTH = 1024;

export async function capture(opts: CaptureOpts = {}): Promise<Capture> {
  const quality = opts.quality ?? DEFAULT_QUALITY;
  const spawner = opts.spawn ?? ((cmd) => Bun.spawn(cmd, { stdout: "pipe", stderr: "pipe" }));

  const args: string[] = ["grim"];
  if (opts.monitor === "focused") {
    // Let grim's default output selection handle focused monitor.
  } else if (opts.monitor && opts.monitor !== "all") {
    args.push("-o", opts.monitor);
  }
  args.push("-t", "jpeg", "-q", String(quality));
  args.push("-"); // stdout

  const proc = spawner(args);
  const stdout = await new Response(proc.stdout as ReadableStream).arrayBuffer();
  const stderr = await new Response(proc.stderr as ReadableStream).text();
  await proc.exited;

  if (proc.exitCode !== 0) {
    throw new Error(`grim failed (exit ${proc.exitCode}): ${stderr}`);
  }

  const jpeg = new Uint8Array(stdout);

  // maxWidth reserved for future downscaling; acknowledge it to satisfy lint.
  const _maxWidth = opts.maxWidth ?? DEFAULT_MAX_WIDTH;
  void _maxWidth;

  return { jpeg };
}

export function toBase64(jpeg: Uint8Array): string {
  return Buffer.from(jpeg).toString("base64");
}
