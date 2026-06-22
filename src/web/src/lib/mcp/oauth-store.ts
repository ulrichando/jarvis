import "server-only";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import type {
  OAuthClientInformationFull,
  OAuthTokens,
} from "@modelcontextprotocol/sdk/shared/auth.js";

// OAuth state for connectors that sign in (Vercel / Notion / etc.). Kept in a
// SEPARATE file from ~/.jarvis/mcp.json so the access token can still be mirrored
// into mcp.json's `headers` (which the voice agent reads) while the refresh
// token + client registration — the sensitive long-lived bits — live here at
// chmod 600. Two sections:
//   - pending: in-flight authorizations keyed by the OAuth `state` (short-lived).
//   - servers: completed auth keyed by the mcp.json server name (for refresh).
const FILE = path.join(os.homedir(), ".jarvis", "mcp-oauth.json");
const PENDING_TTL_MS = 10 * 60_000;

export type Transport = "http" | "sse";

export type PendingAuth = {
  name: string;
  url: string;
  transport: Transport;
  redirectUri: string;
  clientInfo?: OAuthClientInformationFull;
  codeVerifier?: string;
  createdAt: number;
};

export type ServerAuth = {
  url: string;
  transport: Transport;
  redirectUri: string;
  clientInfo: OAuthClientInformationFull;
  tokens: OAuthTokens;
  obtainedAt: number; // ms epoch the tokens were saved (for expiry math)
};

type Shape = { pending: Record<string, PendingAuth>; servers: Record<string, ServerAuth> };

async function read(): Promise<Shape> {
  try {
    const data = JSON.parse(await fs.readFile(FILE, "utf8")) as Partial<Shape>;
    return { pending: data.pending ?? {}, servers: data.servers ?? {} };
  } catch {
    return { pending: {}, servers: {} };
  }
}

async function write(data: Shape): Promise<void> {
  await fs.mkdir(path.dirname(FILE), { recursive: true });
  const tmp = `${FILE}.tmp-${process.pid}`;
  await fs.writeFile(tmp, JSON.stringify(data, null, 2) + "\n", { encoding: "utf8", mode: 0o600 });
  await fs.rename(tmp, FILE); // atomic
  await fs.chmod(FILE, 0o600).catch(() => {});
}

function prune(data: Shape): void {
  const now = Date.now();
  for (const [k, v] of Object.entries(data.pending)) {
    if (now - (v.createdAt ?? 0) > PENDING_TTL_MS) delete data.pending[k];
  }
}

export async function savePending(
  state: string,
  base: { name: string; url: string; transport: Transport; redirectUri: string },
): Promise<void> {
  const data = await read();
  prune(data);
  data.pending[state] = { ...base, createdAt: Date.now() };
  await write(data);
}

export async function patchPending(state: string, patch: Partial<PendingAuth>): Promise<void> {
  const data = await read();
  const cur = data.pending[state];
  if (!cur) return; // patch from the SDK provider before savePending — ignore
  data.pending[state] = { ...cur, ...patch };
  await write(data);
}

export async function getPending(state: string): Promise<PendingAuth | undefined> {
  return (await read()).pending[state];
}

export async function delPending(state: string): Promise<void> {
  const data = await read();
  if (data.pending[state]) {
    delete data.pending[state];
    await write(data);
  }
}

export async function getServerAuth(name: string): Promise<ServerAuth | undefined> {
  return (await read()).servers[name];
}

export async function saveServerAuth(name: string, auth: ServerAuth): Promise<void> {
  const data = await read();
  data.servers[name] = auth;
  await write(data);
}

export async function delServerAuth(name: string): Promise<void> {
  const data = await read();
  if (data.servers[name]) {
    delete data.servers[name];
    await write(data);
  }
}

// True when the access token is expired (or within 60s of it). No expires_in →
// treat as non-expiring; a 401 will trigger a refresh via the auth provider.
export function isExpired(auth: ServerAuth): boolean {
  const exp = auth.tokens.expires_in;
  if (!exp) return false;
  return Date.now() > auth.obtainedAt + (exp - 60) * 1000;
}
