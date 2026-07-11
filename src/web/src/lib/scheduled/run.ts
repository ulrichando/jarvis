import "server-only";

import { generateText } from "ai";
import { randomUUID } from "node:crypto";
import type { UIMessage } from "ai";
import { getModelWithLocalFailover } from "@/lib/ai/local-failover";
import { loadSettings } from "@/lib/settings/store";
import { resolveSharedLocalOwnerId } from "@/lib/auth-helpers";
import {
  ensureConversation,
  recordUsageEvent,
  saveAssistantMessage,
  saveUserMessage,
} from "@/lib/chat/persist";
import {
  dueScheduledChats,
  updateScheduledChat,
  type ScheduledChat,
} from "./store";
import { announceScheduledResult } from "./voice-notify";

// Execute one scheduled chat: run the prompt as a NEW conversation and
// persist it exactly like a hand-typed chat, so it shows up in Recents /
// /chats and can be continued there. This is the Home-side counterpart of
// the code shell's routines — it never touches repos, containers, or the
// bridge.
export async function runScheduledChat(
  task: ScheduledChat,
  // Request-context callers (the run-now route) pass the session user so the
  // conversation lands in THEIR chats; the background tick has no request and
  // falls back to the box owner.
  asUserId?: string,
): Promise<{ conversationId: string | null }> {
  const ownerId = asUserId ?? (await resolveSharedLocalOwnerId());
  const settings = await loadSettings();
  const modelId = task.model ?? settings.defaults.model;
  const { meta, model } = await getModelWithLocalFailover(modelId);

  const conversation = await ensureConversation({
    model: meta.id,
    firstUserText: task.name || task.prompt,
    userId: ownerId ?? undefined,
    // Each scheduled run is a task session → task icon in the sidebar.
    kind: "task",
  });
  if (conversation) {
    const userMessage: UIMessage = {
      id: randomUUID(),
      role: "user",
      parts: [{ type: "text", text: task.prompt }],
    };
    await saveUserMessage({ conversationId: conversation.id, message: userMessage });
  }

  const system =
    settings.defaults.systemPrompt?.trim() ||
    "You are Jarvis. This message is a scheduled task run — produce the requested output directly, no preamble.";
  const result = await generateText({ model, system, prompt: task.prompt });

  if (conversation) {
    await saveAssistantMessage({
      conversationId: conversation.id,
      text: result.text,
      tokensIn: result.usage?.inputTokens,
      tokensOut: result.usage?.outputTokens,
      stopReason: result.finishReason,
    });
    if (ownerId) {
      await recordUsageEvent({
        userId: ownerId,
        conversationId: conversation.id,
        model: meta.id,
        tokensIn: result.usage?.inputTokens,
        tokensOut: result.usage?.outputTokens,
      });
    }
  }

  updateScheduledChat(task.id, {
    last_run_at: Date.now(),
    last_conversation_id: conversation?.id ?? null,
  });

  // Voice reminder: hand the result to the JARVIS voice agent so it speaks
  // "your <task> is ready — want me to read it?" (live) or queues it for the
  // next session connect (offline). Best-effort; never blocks the run result.
  void announceScheduledResult(task.name, result.text);

  return { conversationId: conversation?.id ?? null };
}

/** Background tick: run every due task. Serial on purpose — these are
 *  occasional briefs, not a queue. Returns how many ran. */
export async function runScheduledChatsTick(): Promise<number> {
  const due = dueScheduledChats();
  let ran = 0;
  for (const task of due) {
    try {
      await runScheduledChat(task);
      ran++;
    } catch (err) {
      // Mark the attempt so a permanently-failing task doesn't retry every
      // tick forever (cronIsDue keys off last_run_at).
      updateScheduledChat(task.id, { last_run_at: Date.now() });
      console.error(
        `[scheduled-chats] "${task.name}" failed:`,
        err instanceof Error ? err.message : err,
      );
    }
  }
  return ran;
}
