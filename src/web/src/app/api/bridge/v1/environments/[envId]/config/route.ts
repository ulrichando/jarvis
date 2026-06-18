import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import {
  findEnvironment,
  parseEnvironmentConfig,
  setEnvironmentConfig,
} from "@/lib/bridge/store";
import { getUserId } from "@/lib/auth-helpers";
import { bridgeError } from "@/lib/bridge/errors";

// Environment config (claude.ai/code env config): env vars + a setup script
// applied to that environment's container sessions. Owner-scoped (the vars may
// be secrets). The UI edits .env-format text + a Bash script.

function parseEnvText(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    // Same charset GitHub Actions / dotenv use; skip anything else.
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    out[key] = line.slice(eq + 1).trim();
  }
  return out;
}

function envText(vars: Record<string, string>): string {
  return Object.entries(vars)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

async function authorize(
  req: Request,
  envId: string,
): Promise<{ ok: true } | { res: NextResponse }> {
  const env = findEnvironment(getStore(), envId);
  if (!env) return { res: bridgeError(404, "not_found", "Environment not found") };
  const userId = await getUserId(req.headers);
  // Owner-scoped when the row is owned; legacy null-owner rows stay open.
  if (env.user_id && env.user_id !== userId) {
    return { res: bridgeError(403, "forbidden", "Not your environment") };
  }
  return { ok: true };
}

export async function GET(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<NextResponse> {
  const { envId } = await ctx.params;
  const auth = await authorize(req, envId);
  if ("res" in auth) return auth.res;
  const cfg = parseEnvironmentConfig(findEnvironment(getStore(), envId));
  return NextResponse.json({
    envText: envText(cfg.envVars),
    setupScript: cfg.setupScript,
    networkLevel: cfg.networkLevel,
    customAllowlist: cfg.customAllowlist.join("\n"),
  });
}

export async function PATCH(
  req: Request,
  ctx: { params: Promise<{ envId: string }> },
): Promise<NextResponse> {
  const { envId } = await ctx.params;
  const auth = await authorize(req, envId);
  if ("res" in auth) return auth.res;
  const body = (await req.json().catch(() => null)) as {
    envText?: string;
    setupScript?: string;
    networkLevel?: string;
    customAllowlist?: string;
  } | null;
  if (!body) return bridgeError(400, "invalid_request", "JSON body required");
  const lvl = body.networkLevel;
  setEnvironmentConfig(getStore(), envId, {
    envVars: parseEnvText(typeof body.envText === "string" ? body.envText : ""),
    setupScript: typeof body.setupScript === "string" ? body.setupScript : "",
    networkLevel:
      lvl === "trusted" || lvl === "custom" || lvl === "none" ? lvl : "full",
    customAllowlist:
      typeof body.customAllowlist === "string"
        ? body.customAllowlist
            .split(/[\n,]/)
            .map((d) => d.trim())
            .filter(Boolean)
        : [],
  });
  return NextResponse.json({ ok: true });
}
