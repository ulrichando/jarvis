import { NextResponse } from "next/server";
import {
  getWorkspace,
  updateWorkspaceMeta,
} from "@/lib/workspace/storage";
import {
  createDeployment,
  createProject,
  getProject,
  listDeployments,
  type VercelDeployment,
} from "@/lib/deploy/vercel";

export const runtime = "nodejs";
// Inline deploys can take a while — Vercel processes the upload, runs
// build, returns the deployment record. 5 minutes is the soft cap;
// anything longer the client polls for status separately.
export const maxDuration = 300;

/**
 * GET  /api/workspace/[id]/deploy
 *      → { deployments: VercelDeployment[] | [], provider: "vercel" | null, configured: boolean }
 *      Lists recent deployments. configured=false means the workspace
 *      hasn't been linked to a Vercel project yet.
 *
 * POST /api/workspace/[id]/deploy
 *      body: { target?: "production" | "preview" }
 *      → { deployment: VercelDeployment }
 *      Kicks off a deploy. Auto-creates the Vercel project on first
 *      run if deploy.projectId is unset.
 */

function getToken(envVars: Record<string, string> | undefined): string | null {
  if (!envVars) return null;
  return envVars.VERCEL_TOKEN ?? null;
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const ws = await getWorkspace(id);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });

  if (ws.deploy?.provider !== "vercel") {
    return NextResponse.json({
      provider: null,
      configured: false,
      deployments: [],
    });
  }

  const token = getToken(ws.envVars);
  const projectId = ws.deploy.projectId;

  if (!token || !projectId) {
    return NextResponse.json({
      provider: "vercel",
      configured: false,
      deployments: [],
      hint: !token
        ? "Set VERCEL_TOKEN in Secrets to deploy."
        : "Run a first deploy to initialize the Vercel project.",
    });
  }

  try {
    const deployments = await listDeployments(
      { token, teamId: ws.deploy.teamId },
      { projectId, limit: 10 },
    );
    return NextResponse.json({
      provider: "vercel",
      configured: true,
      deployments,
    });
  } catch (err) {
    return NextResponse.json(
      {
        provider: "vercel",
        configured: true,
        deployments: [],
        error: err instanceof Error ? err.message : String(err),
      },
      { status: 200 }, // soft-fail so the UI can render the error inline
    );
  }
}

export async function POST(
  req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;
  const ws = await getWorkspace(id);
  if (!ws) return NextResponse.json({ error: "not_found" }, { status: 404 });

  const body = (await req.json().catch(() => ({}))) as {
    target?: "production" | "preview";
  };

  const token = getToken(ws.envVars);
  if (!token) {
    return NextResponse.json(
      {
        error: "missing_token",
        hint: "Add VERCEL_TOKEN to Secrets (get one at vercel.com/account/tokens).",
      },
      { status: 400 },
    );
  }

  // Initialize deploy config + project if missing.
  const teamId = ws.deploy?.teamId;
  let projectId = ws.deploy?.projectId;
  let projectName = ws.deploy?.projectName ?? sanitizeProjectName(ws.name);

  if (!projectId) {
    try {
      // Re-use an existing project if the user already created one
      // with this name (avoids the 409 on createProject for repeats).
      const existing = await getProject(
        { token, teamId },
        projectName,
      );
      if (existing) {
        projectId = existing.id;
      } else {
        const created = await createProject(
          { token, teamId },
          { name: projectName },
        );
        projectId = created.id;
        projectName = created.name;
      }
    } catch (err) {
      return NextResponse.json(
        {
          error: "vercel_project_init_failed",
          message: err instanceof Error ? err.message : String(err),
        },
        { status: 502 },
      );
    }
    await updateWorkspaceMeta(id, {
      deploy: {
        provider: "vercel",
        teamId,
        projectId,
        projectName,
      },
    });
  }

  // Now ship the deploy.
  let deployment: VercelDeployment;
  try {
    deployment = await createDeployment(
      { token, teamId },
      {
        projectId,
        projectName,
        workspaceId: id,
        target: body.target ?? "production",
      },
    );
  } catch (err) {
    return NextResponse.json(
      {
        error: "vercel_deploy_failed",
        message: err instanceof Error ? err.message : String(err),
      },
      { status: 502 },
    );
  }

  // Cache the deployment id + production URL on the workspace meta so
  // the Settings UI can render "latest deploy" without an extra API
  // round-trip on mount.
  await updateWorkspaceMeta(id, {
    deploy: {
      provider: "vercel",
      teamId,
      projectId,
      projectName,
      latestDeploymentId: deployment.uid,
      productionUrl: deployment.url,
    },
  });

  return NextResponse.json({ deployment });
}

// Vercel project names: lowercase, alphanumeric + dashes, max 100 chars,
// can't start/end with a dash. Strip everything else.
function sanitizeProjectName(name: string): string {
  const base = name
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return (base || "jarvis-project").slice(0, 100);
}
