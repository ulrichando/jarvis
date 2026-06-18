import "server-only";
import { randomBytes, randomUUID } from "node:crypto";
import {
  appendInbound,
  appendSessionEvent,
  createEnvironment,
  getOrCreateSession,
  listEnvironments,
  setSessionRoutine,
  updateRoutine,
  type RoutineRow,
  type Store,
} from "./store";
import { launchContainerSession } from "./containers";

/**
 * Run a routine now: get-or-create the repo's cloud-container environment,
 * open a session, seed the routine's permission mode / model / instructions,
 * and launch the container. Mirrors the /v1/tasks container dispatch. Returns
 * the new session id, or an error string.
 *
 * Routines run in a cloud container, so a `repo` is required (the schedule/
 * API/GitHub triggers all target a repo, like claude.ai routines).
 */
export async function runRoutine(
  store: Store,
  routine: RoutineRow,
  origin: string,
): Promise<{ sessionId: string } | { error: string }> {
  if (!routine.repo) {
    return { error: "Routine has no repository to run in." };
  }
  const userId = routine.user_id;
  const repoUrl = `https://github.com/${routine.repo}`;

  let environmentId: string;
  const existing = listEnvironments(store, userId).find(
    (e) => e.worker_type === "container" && e.git_repo_url === repoUrl,
  );
  if (existing) {
    environmentId = existing.environment_id;
  } else {
    const created = createEnvironment(store, {
      machine_name: "Cloud container",
      directory: "/workspace",
      git_repo_url: repoUrl,
      max_sessions: 4,
      worker_type: "container",
      user_id: userId,
    });
    environmentId = created.environment_id;
  }

  const sessionId = randomBytes(8).toString("hex");
  getOrCreateSession(store, sessionId, environmentId, routine.name);
  setSessionRoutine(store, sessionId, routine.routine_id);

  // Seed permission mode + model BEFORE the prompt (the child replays inbound
  // from seq 0 in order), then the instructions as the first user message.
  if (routine.permission_mode) {
    const u = randomUUID();
    appendInbound(store, sessionId, {
      type: "control_request",
      uuid: u,
      request_id: u,
      request: { subtype: "set_permission_mode", mode: routine.permission_mode },
    });
  }
  if (routine.model) {
    const u = randomUUID();
    appendInbound(store, sessionId, {
      type: "control_request",
      uuid: u,
      request_id: u,
      request: { subtype: "set_model", model: routine.model },
    });
  }
  const uuid = randomUUID();
  appendInbound(store, sessionId, {
    type: "user",
    uuid,
    session_id: sessionId,
    parent_tool_use_id: null,
    message: { role: "user", content: [{ type: "text", text: routine.instructions }] },
  });
  appendSessionEvent(store, sessionId, {
    type: "user_prompt",
    payload: { type: "user_prompt", prompt: routine.instructions, uuid },
  });

  void launchContainerSession(store, {
    sessionId,
    repoFullName: routine.repo,
    // baseUrl is the bare app origin; launchContainerSession appends the
    // /api/bridge/v1/code/sessions/{id} path itself (was doubled here before).
    baseUrl: origin,
    model: routine.model ?? undefined,
  }).catch(() => {});

  updateRoutine(store, routine.routine_id, { last_run_at: Date.now() });
  return { sessionId };
}
