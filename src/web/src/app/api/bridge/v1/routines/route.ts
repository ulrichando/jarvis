import { NextResponse } from "next/server";
import { randomBytes } from "node:crypto";
import { getStore } from "@/lib/bridge/db";
import {
  createRoutine,
  listRoutines,
  type RoutineTrigger,
} from "@/lib/bridge/store";
import { getUserId } from "@/lib/auth-helpers";
import { validRepoFullName } from "@/lib/bridge/containers";
import { bridgeError } from "@/lib/bridge/errors";

const VALID_MODES = ["default", "acceptEdits", "plan", "bypassPermissions", "dontAsk"];

function rowToApi(r: ReturnType<typeof listRoutines>[number]) {
  let trigger: RoutineTrigger;
  try {
    trigger = JSON.parse(r.trigger_json) as RoutineTrigger;
  } catch {
    trigger = { type: "api", token: "" };
  }
  // Never leak the api trigger token in the list — only whether one exists.
  const safeTrigger =
    trigger.type === "api" ? { type: "api" as const, token: "" } : trigger;
  return {
    routine_id: r.routine_id,
    name: r.name,
    instructions: r.instructions,
    repo: r.repo,
    model: r.model,
    permission_mode: r.permission_mode,
    trigger: safeTrigger,
    paused: !!r.paused,
    created_at: r.created_at,
    last_run_at: r.last_run_at,
    next_run_at: r.next_run_at,
  };
}

// GET /api/bridge/v1/routines — the user's routines.
export async function GET(req: Request): Promise<NextResponse> {
  try {
    const userId = await getUserId(req.headers);
    const routines = listRoutines(getStore(), userId).map(rowToApi);
    return NextResponse.json({ routines });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `DB error: ${msg}`);
  }
}

// POST /api/bridge/v1/routines — create a routine.
export async function POST(req: Request): Promise<NextResponse> {
  const body = (await req.json().catch(() => null)) as {
    name?: string;
    instructions?: string;
    repo?: string | null;
    model?: string | null;
    permission_mode?: string | null;
    trigger?: {
      type?: string;
      cron?: string;
      label?: string;
      events?: string[];
      at?: number;
      filters?: import("@/lib/bridge/store").GithubFilters;
    };
  } | null;

  const name = typeof body?.name === "string" ? body.name.trim() : "";
  const instructions = typeof body?.instructions === "string" ? body.instructions.trim() : "";
  if (!name || !instructions) {
    return bridgeError(400, "invalid_request", "name and instructions are required");
  }
  if (body?.repo && !validRepoFullName(body.repo)) {
    return bridgeError(400, "invalid_request", 'repo must be "owner/name"');
  }

  // Build the trigger. Schedule needs a cron; API mints a token; GitHub takes
  // an event list. Default to API (a webhook the user can POST to).
  let trigger: RoutineTrigger;
  const t = body?.trigger?.type;
  if (t === "schedule") {
    const cron = typeof body?.trigger?.cron === "string" ? body.trigger.cron.trim() : "";
    if (!cron) return bridgeError(400, "invalid_request", "schedule trigger needs a cron expression");
    trigger = {
      type: "schedule",
      cron,
      label: body?.trigger?.label,
      ...(typeof body?.trigger?.at === "number" ? { at: body.trigger.at } : {}),
    };
  } else if (t === "github") {
    const f = body?.trigger?.filters;
    trigger = {
      type: "github",
      events: body?.trigger?.events ?? ["pull_request"],
      ...(f && Object.keys(f).length ? { filters: f } : {}),
    };
  } else {
    trigger = { type: "api", token: `rtk_${randomBytes(24).toString("base64url")}` };
  }

  const mode =
    typeof body?.permission_mode === "string" && VALID_MODES.includes(body.permission_mode)
      ? body.permission_mode
      : "acceptEdits";

  try {
    const userId = await getUserId(req.headers);
    const r = createRoutine(getStore(), {
      name,
      instructions,
      repo: body?.repo ?? null,
      model: typeof body?.model === "string" ? body.model : null,
      permission_mode: mode,
      trigger,
      user_id: userId,
    });
    // Return the token ONCE on create (for API triggers) so the UI can show
    // the webhook URL; subsequent GETs redact it.
    return NextResponse.json(
      {
        routine_id: r.routine_id,
        ...(trigger.type === "api" ? { api_token: trigger.token } : {}),
      },
      { status: 201 },
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return bridgeError(500, "internal_error", `DB error: ${msg}`);
  }
}
