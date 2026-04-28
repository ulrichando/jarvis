import "server-only";

// Server-side wrapper around scripts/lib/docker.mjs so Next API routes
// can share the same container-management code as the PTY server. We
// import dynamically because TS can't currently consume `.mjs` typings
// without extra config — and the runtime is plain ESM either way.

type RuntimeState = {
  state: "running" | "stopped" | "absent";
  ports: Record<string, number>;
};

type ExecResult = {
  stdout: string;
  stderr: string;
  exitCode: number;
  durationMs: number;
};

type DockerLib = {
  dockerAvailable: () => Promise<boolean>;
  imageExists: () => Promise<boolean>;
  inspect: (id: string) => Promise<RuntimeState>;
  ensureRunning: (id: string) => Promise<RuntimeState>;
  stop: (id: string) => Promise<void>;
  destroy: (id: string) => Promise<void>;
  exec: (id: string, command: string, opts?: { timeoutMs?: number }) => Promise<ExecResult>;
  spawnDetached: (id: string, command: string) => Promise<{ execId: string }>;
  containerName: (id: string) => string;
};

let cached: DockerLib | null = null;

async function load(): Promise<DockerLib> {
  if (cached) return cached;
  // Resolve via an absolute file:// URL anchored at cwd. Required because
  // Turbopack bundles route files into `.next/dev/server/chunks/...` and
  // resolves relative imports against that output path — so a static
  // `../../../scripts/...` would point inside `.next/`. Building the URL
  // from process.cwd() at runtime sidesteps the bundler entirely.
  const path = await import("node:path");
  const { pathToFileURL } = await import("node:url");
  const abs = path.resolve(process.cwd(), "scripts/lib/docker.mjs");
  const url = pathToFileURL(abs).href;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mod: any = await import(/* webpackIgnore: true */ url);
  cached = mod;
  return mod;
}

export async function dockerStatus(): Promise<{
  available: boolean;
  imageReady: boolean;
}> {
  const lib = await load();
  const available = await lib.dockerAvailable();
  if (!available) return { available: false, imageReady: false };
  const imageReady = await lib.imageExists();
  return { available, imageReady };
}

export async function getRuntime(id: string): Promise<RuntimeState> {
  const lib = await load();
  return lib.inspect(id);
}

export async function startRuntime(id: string): Promise<RuntimeState> {
  const lib = await load();
  return lib.ensureRunning(id);
}

export async function stopRuntime(id: string): Promise<void> {
  const lib = await load();
  await lib.stop(id);
}

export async function destroyRuntime(id: string): Promise<void> {
  const lib = await load();
  await lib.destroy(id);
}

export async function execInRuntime(
  id: string,
  command: string,
  opts: { timeoutMs?: number } = {},
): Promise<ExecResult> {
  const lib = await load();
  return lib.exec(id, command, opts);
}

export async function spawnDetached(id: string, command: string): Promise<{ execId: string }> {
  const lib = await load();
  return lib.spawnDetached(id, command);
}
