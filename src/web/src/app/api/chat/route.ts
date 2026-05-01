import { convertToModelMessages, streamText, stepCountIs, type UIMessage } from "ai";
import { eq } from "drizzle-orm";
import { getModel, MissingApiKeyError } from "@/lib/ai/models";
import { MODELS_META } from "@/lib/ai/models-meta";
import { loadSettings } from "@/lib/settings/store";
import {
  ensureConversation,
  extractText,
  saveAssistantMessage,
  saveUserMessage,
} from "@/lib/chat/persist";
import { db, schema } from "@/lib/db";
import { getWorkspace, listAllFiles, writeFile } from "@/lib/workspace/storage";
import { generateQuestions, renderQuestionsHtml } from "@/lib/design/questionnaire";
import { buildWorkbenchPrompt, buildDesignPrompt } from "@/lib/actions/jarvis-prompt";
import { getBrand } from "@/lib/design/brand";
import {
  type Format,
  inferAesthetic,
  inferFormat,
  userAskedForQuestions,
} from "@/lib/design/format";
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
  let modelId = model ?? settings.defaults.model;

  // Auto-substitute reasoning models in design mode. Reasoning models
  // (deepseek-reasoner, deepseek-v4-pro, kimi-k2-thinking, o3) burn most
  // of the output budget on hidden thinking tokens, leaving partial
  // artifacts that truncate before the first <boltAction> closes. Design
  // generations need every output token going to actual code. We
  // transparently swap to the configured non-reasoning sibling so the
  // user gets a real result regardless of which model is selected.
  if (mode === "design") {
    const meta = MODELS_META[modelId];
    if (meta?.reasoning && meta.nonReasoningFallback) {
      console.log(
        `[chat] design-mode: substituting ${modelId} → ${meta.nonReasoningFallback} (reasoning models truncate on multi-file artifacts)`,
      );
      modelId = meta.nonReasoningFallback;
    }
  }

  // eslint-disable-next-line no-console
  console.log(
    `[chat] POST mode=${mode ?? "regular"} model=${modelId} msgs=${messages.length} ws=${workspaceId ?? "—"}`,
  );

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
        // ALWAYS ask questions on first turn of a NEW project (empty
        // workspace). Once files exist, we're iterating — skip the
        // questionnaire and design directly per refine-mode rules.
        // Users can also opt-out by including a more specific brief on
        // a later turn, but the very first turn of a brand-new project
        // gets the form so we have real specifics to design from.
        const userTurnCount = messages.filter((m) => m.role === "user").length;
        const existingNow = await listAllFiles(workspaceId);
        const designFilesNow = existingNow.filter(
          (p) => p !== "questions.html" && !p.startsWith("references/"),
        );
        const isFirstTurn = userTurnCount <= 1;
        const isNewProject = designFilesNow.length === 0;
        const needsClarify =
          isFirstTurn &&
          (isNewProject || userAskedForQuestions(firstUserText));

        // Server-side questionnaire bypass: when the brief is sparse,
        // skip the LLM entirely and write a deterministic questions.html.
        // Models (especially deepseek-chat) frequently announce "let me
        // ask questions" then stop without producing the form, or dump
        // the HTML in a markdown code block the parser misses. Doing it
        // server-side: instant, never fails, no model whim. The LLM
        // takes over for the actual design once the user submits answers.
        if (needsClarify) {
          // LLM-backed structured-output question generation. Schema
          // forces a valid JSON shape; we render the HTML server-side.
          // Falls back to format-default questions if the LLM fails.
          const questions = await generateQuestions(
            firstUserText,
            resolvedFormat,
            selected.model,
          );
          const html = renderQuestionsHtml(firstUserText, questions);
          await writeFile(workspaceId, "questions.html", html);
          const replyText =
            "Asked a few quick questions to nail down the design — fill them in and we'll build it.";
          if (conversation) {
            try {
              await saveAssistantMessage({
                conversationId: conversation.id,
                text: replyText,
                tokensIn: 0,
                tokensOut: 0,
                stopReason: "stop",
              });
            } catch (err) {
              console.error("[chat] failed to persist questionnaire reply", err);
            }
          }
          console.log(
            `[chat] design-mode: server-side questionnaire (sparse brief, ${resolvedFormat})`,
          );
          // Hand back a short SSE stream that matches the client's
          // text-delta/finish event format.
          const encoder = new TextEncoder();
          const headers: Record<string, string> = {
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-store",
          };
          if (conversation) headers["X-Conversation-Id"] = conversation.id;
          const stream = new ReadableStream({
            start(controller) {
              controller.enqueue(
                encoder.encode(
                  `data: ${JSON.stringify({ type: "text-delta", delta: replyText })}\n\n`,
                ),
              );
              controller.enqueue(
                encoder.encode(
                  `data: ${JSON.stringify({ type: "finish", finishReason: "stop" })}\n\n`,
                ),
              );
              controller.enqueue(encoder.encode(`data: [DONE]\n\n`));
              controller.close();
            },
          });
          return new Response(stream, { headers });
        }
        // Aesthetic anchor: scan the FULL conversation (not just the
        // current message) so a follow-up like "make the hero pop" still
        // remembers the user said "editorial" three turns back. Brand
        // overrides since it's the strongest constraint.
        const allUserText = messages
          .filter((m) => m.role === "user")
          .map((m) => extractText(m.parts))
          .join("\n");
        const aesthetic = brand ? null : inferAesthetic(allUserText);
        finalSystem += buildDesignPrompt({
          workspaceName: ws.name,
          cwd: "/workspace",
          format: resolvedFormat,
          brand,
          needsClarify,
          aesthetic,
        });

        // Refine mode: when the workspace already has design files, the
        // user is iterating — NOT starting fresh. Without this block the
        // model writes brand-new unrelated files on every turn instead of
        // editing the existing ones. Listing the current paths and stating
        // the iterate-don't-restart rule is what every production tool
        // (Bolt, v0, Lovable) does to keep follow-up turns coherent.
        //
        // Reuses the file scan from the needsClarify check above.
        const designFiles = needsClarify ? [] : designFilesNow;
        if (designFiles.length > 0) {
          finalSystem += `

<existing_design>
  The user already has a design in this workspace — they are ITERATING, not starting from scratch. The current files are:

${designFiles.map((p) => `    ${p}`).join("\n")}

  REFINE-MODE RULES (override anything else that says "create files"):
    - The user's message is a CHANGE request against the design above. Read it as "make this edit to what already exists", not "design a new thing".
    - Rewrite ONLY the file(s) the change actually touches. If the request is "make the hero green", emit one boltAction for the file containing the hero — nothing else.
    - REUSE the existing file paths verbatim. If a Hero component lives at \`components/Hero.jsx\`, the rewrite goes to \`components/Hero.jsx\`. Do NOT invent a new path like \`components/HeroV2.jsx\` or \`hero-new.jsx\`.
    - Do NOT create new files unless the change genuinely requires a new one (e.g. the user says "add a pricing section" → new \`components/Pricing.jsx\`, then update App.jsx to import it).
    - Do NOT re-emit unchanged files. The artifact runner overwrites whatever you ship — every file you include gets replaced. So leave untouched files OUT of the artifact entirely.
    - Provide complete file contents in every boltAction you DO ship — never diffs, never "// rest unchanged".
    - One short sentence of prose before the artifact summarizing the change is fine; nothing after.
</existing_design>`;
        }
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

  // Output-token ceilings tuned per turn type.
  //
  // - Workspace turns (design + workbench) get 32K. v4-pro is a
  //   reasoning model that burns 60-80% of its budget on hidden
  //   thinking; without a generous ceiling, multi-file builds hit
  //   `finish: length` mid-artifact and the client has to auto-continue.
  //   32K accommodates both the reasoning AND a 10+ file output in a
  //   single shot. Providers that cap lower (DeepSeek V3 at ~8K, some
  //   Groq models) silently clamp to their actual max — no harm.
  //
  // - Regular chat turns (no workspace) keep 4K — answers rarely need
  //   more, and going higher just inflates cost on chatty providers.
  const maxOutputTokens = workspaceId ? 32768 : 4096;

  // eslint-disable-next-line no-console
  console.log(
    `[chat] streamText start: system=${finalSystem.length}ch, maxOut=${maxOutputTokens}`,
  );

  const result = streamText({
    model: selected.model,
    system: finalSystem,
    messages: modelMessages,
    temperature: settings.defaults.temperature ?? 0.7,
    maxOutputTokens,
    abortSignal: req.signal,
    // Workspace-scoped turns (design AND workbench) shouldn't have
    // webSearch enabled. Models have been calling it instead of
    // producing the requested artifact (`finish: tool-calls`, no
    // boltActions emitted). Workspace turns are for building code, not
    // research. webSearch stays available only for plain chats.
    tools: workspaceId ? undefined : { webSearch: webSearchTool },
    stopWhen: stepCountIs(5),
    onError: (err) => {
      // streamText surfaces provider errors via this hook — they don't
      // throw out of the function. Without logging we'd silently get an
      // empty stream and the client would just see "loading…" forever.
      console.error("[chat] streamText error:", err);
    },
    onFinish: async ({ text, totalUsage, finishReason }) => {
      if (finishReason === "length") {
        // Length cutoff = the model ran out of tokens mid-output. Log it
        // so we can spot patterns; the client surfaces a toast separately.
        console.warn(
          `[chat] finish=length — output truncated at ${totalUsage.outputTokens} tokens (mode=${mode ?? "regular"})`,
        );
      }
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
