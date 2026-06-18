import "server-only";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { randomBytes } from "node:crypto";

/**
 * The HMAC secret shared by the web token-minter (signProxyToken) and the
 * local CLI proxy verifier (verifyProxyToken). Source of truth is
 * ~/.jarvis/keys.env — the cross-component secret store that the proxy and
 * every launcher already read. The secret NEVER travels over the network:
 * the web app, the proxy, and the CLI are all local to this single-user box
 * and read the same file directly.
 *
 * The WEB is the sole generator (it is the signer that needs the secret
 * first); the proxy only ever reads. Env override wins for tests / CI.
 *
 * keys.env parse + write rules mirror src/cli/src/utils/jarvisKeysEnv.ts
 * (plain KEY=value, last-wins, no quoting, 0600, atomic temp+rename). We keep
 * a minimal local copy rather than importing across the cli/web package
 * boundary.
 */

const SECRET_KEY = "JARVIS_PROXY_JWT_SECRET";
const LINE_KEY_RE = /^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=/;

function keysEnvPath(): string {
  return join(homedir(), ".jarvis", "keys.env");
}

function readKeysEnvValue(key: string, path: string): string | undefined {
  if (!existsSync(path)) return undefined;
  let value: string | undefined;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const m = LINE_KEY_RE.exec(line);
    if (m?.[1] === key) value = line.slice(line.indexOf("=") + 1).trim();
  }
  return value;
}

/** Append the secret line, preserving all existing content. Only ever called
 * when the key is absent, so an append (not an in-place rewrite) is safe. */
function appendSecret(path: string, value: string): void {
  mkdirSync(dirname(path), { recursive: true });
  const existed = existsSync(path);
  const prev = existed ? readFileSync(path, "utf8") : "";
  const sep = prev.length > 0 && !prev.endsWith("\n") ? "\n" : "";
  const next = `${prev}${sep}${SECRET_KEY}=${value}\n`;
  const mode = existed ? statSync(path).mode & 0o777 : 0o600;
  const tmp = `${path}.tmp.${process.pid}`;
  writeFileSync(tmp, next, { mode });
  renameSync(tmp, path);
}

export function getOrCreateProxyJwtSecret(): string {
  const fromEnv = process.env[SECRET_KEY]?.trim();
  if (fromEnv) return fromEnv;
  const path = keysEnvPath();
  const existing = readKeysEnvValue(SECRET_KEY, path);
  if (existing) return existing;
  // 32 random bytes → base64url (keys.env SAFE_VALUE charset).
  const secret = randomBytes(32).toString("base64url");
  appendSecret(path, secret);
  return secret;
}
