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

  // Stash the (capped) result as the reminder text so both the direct voice
  // path and the remote poller can voice it. Cap keeps the JSON store small
  // and the /user-input turn from bloating context.
  const voiceText =
    result.text.length > 2500 ? result.text.slice(0, 2500) + "…" : result.text;
  updateScheduledChat(task.id, {
    last_run_at: Date.now(),
    last_conversation_id: conversation?.id ?? null,
    last_voice_text: voiceText,
  });

  // Voice reminder. On the local box (JARVIS_LOCAL_VOICE=1) deliver directly to
  // the voice agent and mark it delivered so the poller skips it. On a remote
  // instance (the VPS, no local voice client) this is a no-op — the reminder
  // stays "pending voice" for the local poller to pull. Never blocks the run.
  void announceScheduledResult(task.id, task.name, voiceText);

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
