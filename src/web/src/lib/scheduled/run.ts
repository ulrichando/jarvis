import "server-only";

import { generateText, stepCountIs, type ToolSet } from "ai";
import { randomUUID } from "node:crypto";
import type { UIMessage } from "ai";
import { getModelWithLocalFailover } from "@/lib/ai/local-failover";
import { webSearchTool } from "@/lib/tools/web-search";
import { listMcpServers } from "@/lib/mcp/store";
import { loadMcpTools } from "@/lib/mcp/client";
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
  // Fire the voice reminder (default true, for scheduled/background runs). A
  // manual run-now passes false: the caller already has the text (the voice
  // tool reads it, the web UI shows it), so queuing a reminder too would
  // double-speak once the poller pulls it.
  announce = true,
): Promise<{ conversationId: string | null; text: string }> {
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

  // Wire the same data tools the chat has (web search + this instance's MCP
  // connectors) so a "morning brief" can actually gather real data — GitHub,
  // calendar, the Coding-Kiddos MCP, etc. — instead of hallucinating. Tools
  // come from THIS instance's connector config, so a brief that needs a
  // specific MCP must run where that MCP is installed. A broken/slow MCP is
  // skipped (loadMcpTools is per-server try/catch), never breaking the run.
  let mcpTools: ToolSet = {};
  let mcpClose: (() => Promise<void>) | null = null;
  try {
    const servers = await listMcpServers();
    if (servers.some((s) => s.enabled && s.url)) {
      const loaded = await loadMcpTools(servers);
      mcpTools = loaded.tools;
      mcpClose = loaded.close;
    }
  } catch (err) {
    console.error("[scheduled] mcp load failed:", err);
  }

  let result;
  try {
    result = await generateText({
      model,
      system,
      prompt: task.prompt,
      tools: { webSearch: webSearchTool, ...mcpTools },
      // Multi-step: the model calls tools, reads results, then writes the brief.
      stopWhen: stepCountIs(8),
    });
  } finally {
    await mcpClose?.();
  }

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
  if (announce) void announceScheduledResult(task.id, task.name, voiceText);

  return { conversationId: conversation?.id ?? null, text: result.text };
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
