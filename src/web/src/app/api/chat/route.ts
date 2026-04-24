import { convertToModelMessages, streamText, type UIMessage } from "ai";
import { getModel, MissingApiKeyError } from "@/lib/ai/models";
import { loadSettings } from "@/lib/settings/store";
import {
  ensureConversation,
  extractText,
  saveAssistantMessage,
  saveUserMessage,
} from "@/lib/chat/persist";

export const runtime = "nodejs";
export const maxDuration = 60;

const DEFAULT_SYSTEM_PROMPT =
  "You are Jarvis, a personal AI assistant for a power user. Be direct, technical, and concise. Use fenced code blocks with language hints. Use KaTeX ($...$ or $$...$$) for math. Skip pleasantries.";

type Body = {
  id?: string;
  messages: UIMessage[];
  model?: string;
  system?: string;
};

export async function POST(req: Request) {
  const { id, messages, model, system }: Body = await req.json();
  const settings = await loadSettings();
  const modelId = model ?? settings.defaults.model;

  let selected;
  try {
    selected = await getModel(modelId);
  } catch (err) {
    if (err instanceof MissingApiKeyError) {
      return Response.json(
        {
          error: "missing_api_key",
          provider: err.provider,
          message: `Add an API key for ${err.provider} in Settings → Providers to use this model.`,
        },
        { status: 400 },
      );
    }
    throw err;
  }

  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const firstUserText = lastUser ? extractText(lastUser.parts) : "";

  const conversation = await ensureConversation({
    id,
    model: modelId,
    firstUserText,
  });

  if (conversation && lastUser) {
    await saveUserMessage({
      conversationId: conversation.id,
      message: lastUser,
    });
  }

  const modelMessages = await convertToModelMessages(messages);

  const result = streamText({
    model: selected.model,
    system: system ?? settings.defaults.systemPrompt ?? DEFAULT_SYSTEM_PROMPT,
    messages: modelMessages,
    temperature: settings.defaults.temperature ?? 0.7,
    onFinish: async ({ text, totalUsage, finishReason }) => {
      if (!conversation) return;
      try {
        await saveAssistantMessage({
          conversationId: conversation.id,
          text,
          tokensIn: totalUsage.inputTokens,
          tokensOut: totalUsage.outputTokens,
          stopReason: finishReason,
        });
      } catch (err) {
        console.error("[chat] failed to persist assistant message", err);
      }
    },
  });

  const headers: Record<string, string> = {};
  if (conversation) headers["X-Conversation-Id"] = conversation.id;

  return result.toUIMessageStreamResponse({ headers });
}
