import { convertToModelMessages, streamText, stepCountIs, type UIMessage } from "ai";
import { eq } from "drizzle-orm";
import { getModel, MissingApiKeyError } from "@/lib/ai/models";
import { loadSettings } from "@/lib/settings/store";
import {
  ensureConversation,
  extractText,
  saveAssistantMessage,
  saveUserMessage,
} from "@/lib/chat/persist";
import { db, schema } from "@/lib/db";
import { getWorkspace } from "@/lib/workspace/storage";
import { buildWorkbenchPrompt, buildDesignPrompt } from "@/lib/actions/jarvis-prompt";
import { getBrand } from "@/lib/design/brand";
import { type Format, inferFormat } from "@/lib/design/format";
import { webSearchTool } from "@/lib/tools/web-search";

function buildProjectPrompt(p: {
  name: string;
  description: string;
  instructions: string;
}): string {
  const lines: string[] = [];
  lines.push(
    `\n\n# Project context\n\nThis chat lives inside the project "${p.name}".`,
  );
  if (p.description.trim()) {
    lines.push(`\nProject goal: ${p.description.trim()}`);
  }
  if (p.instructions.trim()) {
    lines.push(
      `\nProject instructions (always honor these for this project's chats):\n${p.instructions.trim()}`,
    );
  }
  return lines.join("\n");
}

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

type ChatMode = "design";

type Body = {
  id?: string;
  messages: UIMessage[];
  model?: string;
  system?: string;
  workspaceId?: string;
  mode?: ChatMode;
  format?: Format;
};

export async function POST(req: Request) {
  const { id, messages, model, system, workspaceId, mode, format }: Body = await req.json();
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
      if (mode === "design") {
        const brand = await getBrand(workspaceId);
        // The design tab doesn't show format chips (matching Claude Design's
        // "describe and we'll figure out the shape" UX) so when the client
        // doesn't pass `format`, infer it from the user's latest message.
        const resolvedFormat: Format =
          format ?? inferFormat(firstUserText);
        finalSystem += buildDesignPrompt({
          workspaceName: ws.name,
          cwd: "/workspace",
          format: resolvedFormat,
          brand,
        });
      } else {
        finalSystem += buildWorkbenchPrompt({
          workspaceName: ws.name,
          cwd: "/workspace",
        });
      }
    }
  }

  // If this chat belongs to a Project, mix in the project's name,
  // description, and instructions so every turn shares that context.
  if (db && conversation?.projectId) {
    const [project] = await db
      .select({
        name: schema.projects.name,
        description: schema.projects.description,
        instructions: schema.projects.instructions,
      })
      .from(schema.projects)
      .where(eq(schema.projects.id, conversation.projectId))
      .limit(1);
    if (project) finalSystem += buildProjectPrompt(project);
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
