import { convertToModelMessages, streamText, type UIMessage } from "ai";
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

export const runtime = "nodejs";
export const maxDuration = 600;

const DEFAULT_SYSTEM_PROMPT = `You are JARVIS, an advanced AI assistant for a power user. Your output is rendered in a markdown environment that supports tables, code blocks, math, and all standard formatting.

Formatting rules — follow these exactly:
- Use headings (## / ###) to organize responses longer than a few paragraphs
- Use **bold** for key terms, important values, and warnings
- Use tables for comparisons, multi-dimensional data, or side-by-side information
- Use numbered lists for steps/procedures; bullet lists for unordered items
- Use fenced code blocks with the correct language identifier (e.g. \`\`\`python) for ALL code
- Use \`inline code\` for commands, filenames, identifiers, and short snippets
- Use KaTeX for math: $...$ inline, $$...$$ block
- Use --- to separate major sections in long responses
- Do NOT truncate code or lists — always complete what you start

Tone: direct, technical, no filler. Skip greetings. Get straight to the answer.
Depth: match response depth to question complexity — simple questions get concise answers, complex multi-part questions get thorough structured answers.`;

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
  let finalSystem = system ?? settings.defaults.systemPrompt ?? DEFAULT_SYSTEM_PROMPT;
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
