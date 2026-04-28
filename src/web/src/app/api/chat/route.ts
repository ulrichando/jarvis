import { convertToModelMessages, streamText, stepCountIs, type UIMessage } from "ai";
import { getModel, MissingApiKeyError } from "@/lib/ai/models";
import { loadSettings } from "@/lib/settings/store";
import {
  ensureConversation,
  extractText,
  saveAssistantMessage,
  saveUserMessage,
} from "@/lib/chat/persist";
import { getWorkspace } from "@/lib/workspace/storage";
import { buildWorkbenchPrompt } from "@/lib/actions/jarvis-prompt";
import { webSearchTool } from "@/lib/tools/web-search";

export const runtime = "nodejs";
export const maxDuration = 600;

function buildDefaultSystemPrompt(): string {
  const now = new Date();
  const dateStr = now.toUTCString(); // e.g. "Mon, 27 Apr 2026 14:32:00 GMT"
  return `You are JARVIS, an advanced AI assistant. Current date/time: ${dateStr}.

Simple questions → short answers. "What time is it in X?" → one line with the actual time calculated from UTC. "What's 2+2?" → "4". No preamble, no caveats, no restating the question.

Complex questions → structured answers using markdown: headings (##/###), **bold**, tables, numbered lists, fenced code blocks with language tags, \`inline code\`. Complete all code and lists — never truncate.

KaTeX ($...$ or $$...$$) for mathematical expressions ONLY. Never use math notation for text, timezone names, abbreviations, or anything non-mathematical.

Skip greetings and filler. Never start with "Certainly", "Of course", "Great question", or any opener. Just answer.`;
}

type Body = {
  id?: string;
  messages: UIMessage[];
  model?: string;
  system?: string;
  workspaceId?: string;
};

export async function POST(req: Request) {
  const { id, messages, model, system, workspaceId }: Body = await req.json();
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

  // If the chat is targeting a workspace, append the bolt-style artifact
  // instructions so the model emits <boltArtifact>/<boltAction> blocks
  // that our streaming parser (lib/actions/message-parser.ts) can route
  // into file writes and shell execs.
  let finalSystem = system ?? settings.defaults.systemPrompt ?? buildDefaultSystemPrompt();
  if (workspaceId) {
    const ws = await getWorkspace(workspaceId);
    if (ws) {
      finalSystem += buildWorkbenchPrompt({
        workspaceName: ws.name,
        cwd: "/workspace",
      });
    }
  }

  const result = streamText({
    model: selected.model,
    system: finalSystem,
    messages: modelMessages,
    temperature: settings.defaults.temperature ?? 0.7,
    abortSignal: req.signal,
    tools: { webSearch: webSearchTool },
    stopWhen: stepCountIs(5),
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
