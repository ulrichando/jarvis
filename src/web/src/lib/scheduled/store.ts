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
  patch: Partial<Pick<ScheduledChat, "name" | "prompt" | "cron" | "label" | "at" | "model" | "paused" | "last_run_at" | "last_conversation_id">>,
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

/** Tasks due right now: one-time `at` reached and never run, or cron due
 *  since the last run (2h lookback, same semantics as the code routines). */
export function dueScheduledChats(now = Date.now()): ScheduledChat[] {
  return readAll().filter((t) => {
    if (t.paused) return false;
    if (t.at) return t.at <= now && !t.last_run_at;
    return cronIsDue(t.cron, t.last_run_at, now);
  });
}
