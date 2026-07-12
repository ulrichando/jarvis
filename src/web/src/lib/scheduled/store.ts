import "server-only";

import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { cronIsDue } from "@/lib/cron";

// Scheduled CHATS — a Home feature, deliberately separate from the code
// shell's repo routines (/api/bridge/v1/routines). Each task runs a prompt
// as a normal conversation on a cron; results land in the user's chats.
export type ScheduledChat = {
  id: string;
  name: string;
  prompt: string;
  /** Model id; absent = the user's default chat model at run time. */
  model?: string;
  cron: string;
  /** Human schedule label ("Daily at 9:00"). */
  label: string;
  /** One-time run at this epoch ms (cron is then just a formality). */
  at?: number;
  paused: boolean;
  created_at: number;
  last_run_at: number | null;
  last_conversation_id: string | null;
  // Voice-reminder delivery tracking. `last_voice_text` is the (capped)
  // spoken text set when the task fires; a reminder is "pending voice" when
  // last_run_at > voice_delivered_at. The local voice poller fetches pending
  // ones from a remote (VPS) instance and marks them delivered.
  last_voice_text?: string | null;
  voice_delivered_at?: number | null;
};

// ponytail: JSON file store like ~/.jarvis/workspaces/_meta.json — no DB
// migration needed; move to Postgres if this ever needs multi-user.
const FILE = join(homedir(), ".jarvis", "scheduled-chats.json");

function readAll(): ScheduledChat[] {
  try {
    const parsed = JSON.parse(readFileSync(FILE, "utf8")) as {
      tasks?: ScheduledChat[];
    };
    return Array.isArray(parsed.tasks) ? parsed.tasks : [];
  } catch {
    return [];
  }
}

function writeAll(tasks: ScheduledChat[]) {
  mkdirSync(join(homedir(), ".jarvis"), { recursive: true });
  const tmp = `${FILE}.tmp`;
  writeFileSync(tmp, JSON.stringify({ tasks }, null, 2));
  renameSync(tmp, FILE);
}

export function listScheduledChats(): ScheduledChat[] {
  return readAll();
}

export function createScheduledChat(input: {
  name: string;
  prompt: string;
  cron: string;
  label: string;
  at?: number;
  model?: string;
}): ScheduledChat {
  const task: ScheduledChat = {
    id: randomUUID(),
    name: input.name,
    prompt: input.prompt,
    cron: input.cron,
    label: input.label,
    ...(input.at ? { at: input.at } : {}),
    ...(input.model ? { model: input.model } : {}),
    paused: false,
    created_at: Date.now(),
    last_run_at: null,
    last_conversation_id: null,
  };
  writeAll([...readAll(), task]);
  return task;
}

export function updateScheduledChat(
  id: string,
  patch: Partial<Pick<ScheduledChat, "name" | "prompt" | "cron" | "label" | "at" | "model" | "paused" | "last_run_at" | "last_conversation_id" | "last_voice_text" | "voice_delivered_at">>,
): ScheduledChat | null {
  const tasks = readAll();
  const idx = tasks.findIndex((t) => t.id === id);
  if (idx === -1) return null;
  tasks[idx] = { ...tasks[idx], ...patch };
  writeAll(tasks);
  return tasks[idx];
}

export function deleteScheduledChat(id: string): boolean {
  const tasks = readAll();
  const next = tasks.filter((t) => t.id !== id);
  if (next.length === tasks.length) return false;
  writeAll(next);
  return true;
}

/** Reminders that have fired but haven't been voiced yet, marked delivered in
 *  the same read (so a poll never double-delivers). Used by the voice-pending
 *  endpoint the local poller pulls. */
export function takePendingVoiceReminders(
  now = Date.now(),
): Array<{ id: string; name: string; text: string }> {
  const tasks = readAll();
  const out: Array<{ id: string; name: string; text: string }> = [];
  let changed = false;
  for (const t of tasks) {
    if (
      t.last_run_at &&
      t.last_voice_text &&
      t.last_run_at > (t.voice_delivered_at ?? 0)
    ) {
      out.push({ id: t.id, name: t.name, text: t.last_voice_text });
      t.voice_delivered_at = now;
      changed = true;
    }
  }
  if (changed) writeAll(tasks);
  return out;
}

/** Tasks due right now: one-time `at` reached and never run, or cron due
 *  since the last run (2h lookback, same semantics as the code routines). */
export function dueScheduledChats(now = Date.now()): ScheduledChat[] {
  return readAll().filter((t) => {
    if (t.paused) return false;
    if (t.at) return t.at <= now && !t.last_run_at;
    return cronIsDue(t.cron, t.last_run_at, now);
  });
}
