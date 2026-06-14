import { NextResponse } from "next/server";
import {
  getWorkspace,
  deleteWorkspace,
  renameWorkspace,
  updateWorkspaceMeta,
  maskEnvVars,
  type Workspace,
} from "@/lib/workspace/storage";
import { destroyRuntime, dockerStatus } from "@/lib/workspace/docker";

export const runtime = "nodejs";

// Strip secret-class env values out of GET responses by default.
// The Settings UI explicitly opts in to revealing a value via
// `?revealEnv=KEY` so the rest of the network never sees the
// plaintext key/token/dsn unless the operator clicked "reveal".
function publicShape(ws: Workspace, revealEnvKeys?: string[]) {
  const { envVars, ...rest } = ws;
  const masked = maskEnvVars(envVars);
  if (revealEnvKeys && envVars) {
    for (const k of revealEnvKeys) {
      if (k in envVars) masked[k] = { value: envVars[k], masked: false };
    }
  }
  return { ...rest, envVars: masked };
}

export async function GET(req: Request, ctx: RouteContext<"/api/workspace/[id]">) {
  const { id } = await ctx.params;
  const ws = await getWorkspace(id);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });
  const url = new URL(req.url);
  const reveal = url.searchParams.getAll("revealEnv");
  return NextResponse.json({ workspace: publicShape(ws, reveal) });
}

export async function PATCH(req: Request, ctx: RouteContext<"/api/workspace/[id]">) {
  const { id } = await ctx.params;
  const body = (await req.json().catch(() => ({}))) as {
    name?: unknown;
    customInstructions?: unknown;
    envVars?: unknown;
    removeEnvKeys?: unknown;
    devCommand?: unknown;
    deploy?: unknown;
    auth?: unknown;
  };

  // Name (legacy single-field rename — kept for back-compat).
  if (typeof body.name === "string") {
    const name = body.name;
    if (!name.trim() || name.length > 80) {
      return NextResponse.json({ error: "invalid name" }, { status: 400 });
    }
    const renamed = await renameWorkspace(id, name);
    if (!renamed) return NextResponse.json({ error: "not_found" }, { status: 404 });
    // If the request ONLY contained name, return early.
    const otherKeys = ["customInstructions", "envVars", "removeEnvKeys", "devCommand", "deploy", "auth"] as const;
    if (otherKeys.every((k) => body[k] === undefined)) {
      return NextResponse.json({ workspace: publicShape(renamed) });
    }
  }

  // Other fields — validated inside updateWorkspaceMeta.
  const patch: Parameters<typeof updateWorkspaceMeta>[1] = {};
  if (typeof body.customInstructions === "string") {
    patch.customInstructions = body.customInstructions;
  }
  if (
    body.envVars &&
    typeof body.envVars === "object" &&
    !Array.isArray(body.envVars)
  ) {
    patch.envVars = body.envVars as Record<string, string>;
  }
  if (Array.isArray(body.removeEnvKeys)) {
    patch.removeEnvKeys = body.removeEnvKeys.filter(
      (k): k is string => typeof k === "string",
    );
  }
  if (typeof body.devCommand === "string") {
    patch.devCommand = body.devCommand;
  }
  if (
    body.deploy &&
    typeof body.deploy === "object" &&
    !Array.isArray(body.deploy)
  ) {
    const d = body.deploy as Record<string, unknown>;
    if (d.provider === "vercel") {
      patch.deploy = {
        provider: "vercel",
        teamId:
          typeof d.teamId === "string" && d.teamId ? d.teamId : undefined,
        projectId:
          typeof d.projectId === "string" && d.projectId
            ? d.projectId
            : undefined,
        projectName:
          typeof d.projectName === "string" && d.projectName
            ? d.projectName
            : undefined,
      };
    }
  }
  if (
    body.auth &&
    typeof body.auth === "object" &&
    !Array.isArray(body.auth)
  ) {
    const a = body.auth as Record<string, unknown>;
    patch.auth = {
      providers: Array.isArray(a.providers)
        ? (a.providers.filter((p) => typeof p === "string") as Array<
            "credentials" | "magic-link" | "google" | "github"
          >)
        : [],
      sessionMins:
        typeof a.sessionMins === "number" ? a.sessionMins : 1440,
      cookieSecure: !!a.cookieSecure,
      cookieSameSite:
        a.cookieSameSite === "strict" ||
        a.cookieSameSite === "none" ||
        a.cookieSameSite === "lax"
          ? a.cookieSameSite
          : "lax",
    };
  }
  if (Object.keys(patch).length > 0) {
    const updated = await updateWorkspaceMeta(id, patch);
    if (!updated) return NextResponse.json({ error: "not_found" }, { status: 404 });
    return NextResponse.json({ workspace: publicShape(updated) });
  }

  // No-op — nothing to update.
  const ws = await getWorkspace(id);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });
  return NextResponse.json({ workspace: publicShape(ws) });
}

export async function DELETE(_req: Request, ctx: RouteContext<"/api/workspace/[id]">) {
  const { id } = await ctx.params;
  // Tear down the sandbox container first so we don't orphan it. A failed
  // teardown is non-fatal for the on-disk delete (if docker isn't running
  // we still nuke the workspace dir), but it must NOT be swallowed: a
  // running container with no UI handle leaks resources forever. Log it,
  // retry once, and report the outcome so the client can warn.
  let containerTeardown: "ok" | "failed" | "skipped" = "skipped";
  try {
    const s = await dockerStatus();
    if (s.available) {
      try {
        await destroyRuntime(id);
        containerTeardown = "ok";
      } catch (err) {
        console.warn(`[workspace] destroyRuntime failed for ${id}, retrying:`, err);
        try {
          await destroyRuntime(id);
          containerTeardown = "ok";
        } catch (err2) {
          containerTeardown = "failed";
          console.error(
            `[workspace] destroyRuntime failed twice for ${id}; container may be orphaned:`,
            err2,
          );
        }
      }
    }
  } catch (err) {
    // dockerStatus itself failed — can't confirm whether a container runs.
    containerTeardown = "failed";
    console.error(`[workspace] dockerStatus failed during delete of ${id}:`, err);
  }
  await deleteWorkspace(id);
  return NextResponse.json({ ok: true, containerTeardown });
}
