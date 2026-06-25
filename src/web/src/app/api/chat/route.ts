import { convertToModelMessages, streamText, stepCountIs, type UIMessage, type ToolSet } from "ai";
import { eq } from "drizzle-orm";
import { getModel, MissingApiKeyError } from "@/lib/ai/models";
import { MODELS_META } from "@/lib/ai/models-meta";
import { loadSettings } from "@/lib/settings/store";
import {
  ensureConversation,
  extractText,
  maybeUpdateLastAssistantMessage,
  saveAssistantMessage,
  saveUserMessage,
} from "@/lib/chat/persist";
import { requireUserId, Unauthenticated } from "@/lib/auth-helpers";
import { listMcpServers } from "@/lib/mcp/store";
import { loadMcpTools } from "@/lib/mcp/client";
import { db, schema } from "@/lib/db";
import {
  getWorkspace,
  listAllFiles,
  setWorkspaceConversation,
  writeFile,
} from "@/lib/workspace/storage";
import { readKnowledgeBlock } from "@/lib/workspace/knowledge";
import { readGlobalKnowledgeBlock } from "@/lib/knowledge/store";
import { generateQuestions, renderQuestionsHtml } from "@/lib/design/questionnaire";
import { buildWorkbenchPrompt, buildDesignPrompt, buildArtifactPrompt } from "@/lib/actions/jarvis-prompt";
import { extractJarvisArtifacts } from "@/lib/artifacts/extract";
import { detectArtifacts } from "@/lib/artifacts/detect";
import { upsertArtifactFromMessage } from "@/lib/artifacts/store";
import { buildDesignContextBlock } from "@/lib/design-context";
import { getBrand } from "@/lib/design/brand";
import {
  AESTHETICS,
  type Aesthetic,
  type Format,
  inferAesthetic,
  inferFormat,
  userAskedForQuestions,
} from "@/lib/design/format";
import { webSearchTool } from "@/lib/tools/web-search";
import { createGenerateImageTool } from "@/lib/tools/generate-image";
import {
  imageGenAvailable,
  generateChatImage,
  type GeneratedImage,
} from "@/lib/ai/image";
import {
  appendImageMarkdown,
  stripGeneratedImagesForModel,
  hasImageIntent,
} from "@/lib/chat/image-markdown";

// File extension → suggested filename when the model dumps a fenced
// code block instead of using boltAction. Used by the recovery path
// in onFinish: if the assistant text contains ```language fences but
// no <boltArtifact>, we treat each fence as a candidate file write so
// the user gets SOMETHING on disk instead of an empty workspace.
const FENCE_LANG_TO_FILE: Record<string, string> = {
  html: "index.html",
  jsx: "App.jsx",
  tsx: "App.tsx",
  js: "main.js",
  ts: "main.ts",
  css: "styles.css",
  json: "data.json",
};

const FENCE_RE = /```([a-zA-Z0-9]+)\n([\s\S]*?)```/g;
// Unclosed fence at the end of a truncated stream. We grab everything
// from the opening fence to end-of-text. Used with .exec() / .search()
// (NOT .match() which would only match at offset 0) so it finds the
// fence wherever it appears in the tail.
const UNCLOSED_FENCE_RE = /```([a-zA-Z0-9]+)\n([\s\S]*)$/;
const ARTIFACT_RE = /<boltArtifact\b/i;

/**
 * Recover files when the model forgot the bolt protocol and dumped
 * code in markdown fences. Returns the count of files written so the
 * caller can log a warning.
 *
 * Heuristic: if the text contains at least one ```html|jsx|tsx|...
 * fenced block AND no `<boltArtifact>` tag, write each fenced block
 * to a sensible default filename (index.html for html, App.jsx for
 * jsx, etc.). Multiple blocks of the same language get suffixed
 * (e.g. App.jsx → App.2.jsx). Files that already exist are skipped.
 */
async function recoverFencesAsFiles(
  workspaceId: string,
  assistantText: string,
): Promise<{ written: number; skipped: number }> {
  if (ARTIFACT_RE.test(assistantText)) return { written: 0, skipped: 0 };
  let written = 0;
  let skipped = 0;
  const counts: Record<string, number> = {};
  let lastClosedEnd = 0;
  for (const match of assistantText.matchAll(FENCE_RE)) {
    const lang = match[1].toLowerCase();
    const body = match[2];
    const baseName = FENCE_LANG_TO_FILE[lang];
    if (match.index !== undefined) {
      lastClosedEnd = match.index + match[0].length;
    }
    if (!baseName || !body.trim()) {
      skipped += 1;
      continue;
    }
    counts[baseName] = (counts[baseName] ?? 0) + 1;
    const fileName =
      counts[baseName] === 1
        ? baseName
        : baseName.replace(/\.([a-z]+)$/, `.${counts[baseName]}.$1`);
    try {
      await writeFile(workspaceId, fileName, body);
      written += 1;
    } catch (err) {
      console.warn(`[chat] fence-recovery: writeFile ${fileName} failed:`, err);
      skipped += 1;
    }
  }
  // Unclosed-fence recovery: when finish=length truncated the stream
  // mid-file, the opening ```html arrived but the closing ``` did not.
  // Look in the tail (after the last closed fence) for an opening
  // fence and treat everything after it as the file body. Without this
  // the user loses the entire half-finished artifact.
  const tail = assistantText.slice(lastClosedEnd);
  // .exec finds the pattern anywhere in `tail`, unlike .match which is
  // anchored to offset 0 when the regex itself has no leading anchor.
  const unclosed = UNCLOSED_FENCE_RE.exec(tail);
  if (unclosed) {
    const lang = unclosed[1].toLowerCase();
    const body = unclosed[2];
    const baseName = FENCE_LANG_TO_FILE[lang];
    if (baseName && body.trim()) {
      counts[baseName] = (counts[baseName] ?? 0) + 1;
      const fileName =
        counts[baseName] === 1
          ? baseName
          : baseName.replace(/\.([a-z]+)$/, `.${counts[baseName]}.$1`);
      try {
        await writeFile(workspaceId, fileName, body);
        written += 1;
        console.warn(
          `[chat] fence-recovery: rescued UNCLOSED ${lang} fence from truncated stream → ${fileName} (${body.length} chars)`,
        );
      } catch (err) {
        console.warn(
          `[chat] fence-recovery: writeFile ${fileName} failed:`,
          err,
        );
        skipped += 1;
      }
    }
  }
  return { written, skipped };
}

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

/**
 * Personalization block from the user's profile (Settings → General). These
 * fields (name / callName / jobTitle / preferences) were saved but never
 * reached the model — so the assistant didn't know who it was talking to or
 * how to address them. Appended to the system prompt so they actually apply.
 */
function buildUserContextPrompt(user: {
  name?: string;
  callName?: string;
  jobTitle?: string;
  preferences?: string;
}): string {
  const lines: string[] = [];
  const name = user.name?.trim();
  const callName = user.callName?.trim();
  const jobTitle = user.jobTitle?.trim();
  const preferences = user.preferences?.trim();
  if (name) lines.push(`The user's name is ${name}.`);
  if (callName) lines.push(`Address them as "${callName}".`);
  if (jobTitle) lines.push(`Their role / title: ${jobTitle}.`);
  if (preferences)
    lines.push(`Their stated preferences — honor these:\n${preferences}`);
  if (lines.length === 0) return "";
  return `\n\n# About the user you're talking to\n${lines.join("\n")}`;
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
  // DeepSeek "Search" composer toggle — false hides the web-search tool.
  search?: boolean;
  // Universal "Image" composer toggle — true forces server-side image
  // generation for this turn (model-independent), matching Open WebUI's
  // explicit image mode. Works even with thinking models that can't tool-call.
  image?: boolean;
};

export async function POST(req: Request) {
  const { id, messages, model, system, workspaceId, mode, format, search, image }: Body = await req.json();
  let userId: string;
  try {
    userId = await requireUserId(req.headers);
  } catch (e) {
    if (e instanceof Unauthenticated) return new Response("Unauthorized", { status: 401 });
    throw e;
  }
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

  // Workbench-mode model routing. Same problem as design mode but for
  // multi-file builds: reasoning models burn output budget on hidden
  // thinking and stall on commits. Two override paths, in priority:
  //   1. JARVIS_WORKBENCH_MODEL env var — admin/user-configured "always
  //      use this model for workspace turns" (e.g. claude-sonnet-4-6).
  //      If the configured model is unknown or has no API key, we fall
  //      through to the user's pick rather than 400-erroring.
  //   2. Reasoning-model fallback (same as design mode).
  // This is the same "smart routing" Cursor and Bolt do — diagnose with
  // a fast cheap model, write with a stronger committal one.
  if (workspaceId && !mode) {
    const envOverride = process.env.JARVIS_WORKBENCH_MODEL;
    if (envOverride && MODELS_META[envOverride]) {
      if (envOverride !== modelId) {
        console.log(
          `[chat] workbench-mode: override ${modelId} → ${envOverride} (JARVIS_WORKBENCH_MODEL)`,
        );
        modelId = envOverride;
      }
    } else {
      const meta = MODELS_META[modelId];
      if (meta?.reasoning && meta.nonReasoningFallback) {
        console.log(
          `[chat] workbench-mode: substituting ${modelId} → ${meta.nonReasoningFallback} (reasoning model — would burn output budget on thinking instead of files)`,
        );
        modelId = meta.nonReasoningFallback;
      }
    }
  }

  // eslint-disable-next-line no-console
  console.log(
    `[chat] POST mode=${mode ?? "regular"} model=${modelId} msgs=${messages.length} ws=${workspaceId ?? "—"}`,
  );

  // K2.6 mode-aware routing. Each kimi-k2-{instant,thinking,agent,swarm}
  // model id maps to the same upstream API but needs different params /
  // orchestration to deliver the user-facing semantics. The full
  // dispatcher lives in src/lib/ai/kimi/. Gated by KIMI_K2_MODES_ENABLED
  // so we can roll it out behind a flag and revert in one env-var flip.
  if (modelId.startsWith("kimi-k2-") && process.env.KIMI_K2_MODES_ENABLED === "1") {
    const { routeKimiMode } = await import("@/lib/ai/kimi");
    return routeKimiMode({ messages, model, system }, modelId);
  }

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
    userId,
  });

  // Pin conversation→workspace server-side so refresh / different-
  // browser still resolves the same chat history. Was localStorage-only
  // before; that broke the moment a user opened the workspace from
  // anywhere else (or after clearing site data).
  if (conversation && workspaceId) {
    try {
      await setWorkspaceConversation(workspaceId, conversation.id);
    } catch (err) {
      console.warn("[chat] setWorkspaceConversation failed:", err);
    }
  }

  if (conversation && lastUser) {
    // Before saving the new user message, persist any client-side
    // enrichment of the PREVIOUS assistant message (e.g. the
    // <boltActionResults> block the chat layer appends after actions
    // finish). Without this, refresh wipes the results from history
    // and the model loses its ground truth across turns — that's the
    // "Jarvis keeps stopping in diagnose loops" symptom. Find the most
    // recent assistant message in the request and reconcile its text
    // with the DB.
    const lastAssistantInReq = [...messages]
      .reverse()
      .find((m) => m.role === "assistant");
    if (lastAssistantInReq) {
      try {
        await maybeUpdateLastAssistantMessage({
          conversationId: conversation.id,
          candidate: lastAssistantInReq,
        });
      } catch (err) {
        console.error(
          "[chat] failed to persist enriched assistant message",
          err,
        );
      }
    }
    // Skip persisting the chat layer's synthetic auto-continue prompt.
    // When the model hits finish=length mid-output, the client appends
    // a user message with the canary string below to nudge the next
    // streamText call to extend the partial reply. That's PLUMBING —
    // the user never typed it. Persisting it would leak the system
    // prompt into the visible chat history, which the user reported
    // as "code is leaking on the UI" (the message reads like a stage
    // direction next to their real prompts on refresh).
    //
    // The canary check is intentionally tight: if the user genuinely
    // typed this 40-character prefix verbatim, they meant it; we
    // don't want to silently drop their input.
    const lastUserText = extractText(lastUser.parts).trim();
    const AUTO_CONTINUE_CANARY =
      "Continue your previous output exactly where you stopped";
    const isAutoContinueSynthetic =
      lastUserText.startsWith(AUTO_CONTINUE_CANARY) &&
      lastUserText.includes("Close any open boltAction");
    if (!isAutoContinueSynthetic) {
      await saveUserMessage({
        conversationId: conversation.id,
        message: lastUser,
      });
    } else {
      console.log(
        "[chat] skipping persistence of auto-continue synthetic prompt",
      );
    }
  }

  // Strip generated-image markdown from the assistant HISTORY before it goes to
  // the model. The rendered `![..](/api/media/..)` url is DISPLAY-only; feeding
  // it back teaches the model to mimic the pattern and hallucinate fake
  // /api/media urls (which 404 → broken images) instead of calling the tool.
  // Replaced with a neutral placeholder so the model still knows an image was
  // made. Does NOT touch what the user sees or what we persist.
  const historyForModel = messages.map((m) =>
    m.role === "assistant"
      ? {
          ...m,
          parts: m.parts.map((p) =>
            p.type === "text"
              ? { ...p, text: stripGeneratedImagesForModel(p.text) }
              : p,
          ),
        }
      : m,
  );
  const modelMessages = await convertToModelMessages(historyForModel);

  // If the chat is targeting a workspace, append the bolt-style artifact
  // instructions so the model emits <boltArtifact>/<boltAction> blocks
  // that our streaming parser (lib/actions/message-parser.ts) can route
  // into file writes and shell execs.
  let finalSystem = system ?? settings.defaults.systemPrompt ?? buildDefaultSystemPrompt();
  // Personalization: make the user's profile (name/callName/jobTitle/
  // preferences) actually reach the assistant. No-op when the profile is empty.
  finalSystem += buildUserContextPrompt(settings.user ?? {});
  // Global knowledge docs (Settings → Knowledge) — reference material the user
  // uploaded, injected into every chat. No-op when none are enabled.
  finalSystem += await readGlobalKnowledgeBlock();
  // Plain chat (no workspace): activate claude.ai-style self-contained
  // artifacts so the model can emit <jarvisArtifact> blocks the artifact
  // side panel renders + persists. Workspace turns keep the bolt path
  // below. Kill-switch: JARVIS_WEB_ARTIFACTS_ENABLED=0.
  if (!workspaceId && process.env.JARVIS_WEB_ARTIFACTS_ENABLED !== "0") {
    finalSystem += buildArtifactPrompt();
  }
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
        let aesthetic: Aesthetic | null = brand
          ? null
          : inferAesthetic(allUserText);
        // If neither a brand nor a keyword pinned the aesthetic, pick
        // one DETERMINISTICALLY from the workspaceId. Without this the
        // model defaults to its training bias (usually editorial dark
        // serif) for every fresh build — which is why the user
        // perceived jarvis as "only knowing one style." Hashing the
        // workspaceId means each workspace gets a stable, distinct
        // aesthetic, but you still get the same look across turns
        // within a workspace (no flicker between builds).
        // Workspace-id hash — used both for the aesthetic fallback AND
        // as the base of the theme rotation seed. Same workspace → same
        // hash → consistent style across "edit this" follow-ups.
        let wsHash = 0;
        for (let i = 0; i < workspaceId.length; i++) {
          wsHash = (wsHash * 31 + workspaceId.charCodeAt(i)) | 0;
        }
        if (!aesthetic && !brand) {
          const idx = Math.abs(wsHash) % AESTHETICS.length;
          aesthetic = AESTHETICS[idx] as Aesthetic;
          console.log(
            `[chat] design-mode: rotated aesthetic → ${aesthetic} (no brand, no keyword match in brief)`,
          );
        }
        // Theme seed: stable per workspace by default. Bumped each
        // time the user asks for a "redesign" / "different style" /
        // "another version" so follow-up turns rotate to a different
        // colorway WITHIN the same aesthetic. Without this, "redesign"
        // returns identical hex values because the theme picker is
        // locked 1:1 to aesthetic. Count redesign-intent words across
        // the full conversation so the bump is monotonic.
        const REDESIGN_RE =
          /\b(redesign|redo|different\s+(style|look|version|colors|theme)|another\s+(version|take|look)|new\s+look|fresh\s+(take|look)|change\s+(the\s+)?(style|theme|colors)|swap\s+(the\s+)?(theme|colors|palette)|make\s+it\s+look\s+different)\b/i;
        const redesignCount = messages.filter(
          (m) => m.role === "user" && REDESIGN_RE.test(extractText(m.parts)),
        ).length;
        const themeSeed = wsHash * 7 + redesignCount;
        if (redesignCount > 0) {
          console.log(
            `[chat] design-mode: redesign signals detected (${redesignCount}) — themeSeed=${themeSeed}`,
          );
        }
        finalSystem += buildDesignPrompt({
          workspaceName: ws.name,
          cwd: "/workspace",
          format: resolvedFormat,
          brand,
          needsClarify,
          aesthetic,
          themeSeed,
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
        // Auto-embed the workspace's design/ folder into context so
        // jarvis has the visual reference inline from turn 1 — no
        // need to spend an LLM turn cat'ing files. Bolt + Lovable do
        // the same with Figma context. Empty string when there's no
        // design/ directory; cheap (~10ms) when there is.
        try {
          const designBlock = await buildDesignContextBlock(workspaceId);
          if (designBlock) finalSystem += designBlock;
        } catch (err) {
          console.warn("[chat] design-context load failed:", err);
        }
      }
    }
  }

  // Workspace-scoped custom instructions (the .cursorrules / CLAUDE.md
  // pattern). Editable from the workbench Settings tab. Trimmed to
  // 8K on save in updateWorkspaceMeta. Appended LAST so workspace-
  // specific rules override more general ones from defaults / mode
  // prompts above.
  if (workspaceId) {
    const ws = await getWorkspace(workspaceId);
    if (ws?.customInstructions) {
      finalSystem += `\n\n## Custom instructions for this workspace\n${ws.customInstructions}\n`;
    }
    // Workspace knowledge — uploaded reference docs (CV, brand guide,
    // API contract, etc.). Each enabled doc is read whole, truncated
    // to 4K chars, concatenated into a "## Workspace knowledge"
    // section. Treated as authoritative project facts. Settings →
    // Knowledge manages the docs.
    const knowledge = await readKnowledgeBlock(workspaceId);
    if (knowledge) finalSystem += knowledge;
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
  // - Workspace turns (design + workbench) WANT 32K so multi-file
  //   builds don't hit `finish: length` mid-artifact. But several
  //   providers reject high caps with hard errors instead of silently
  //   clamping (Groq Llama 4 Scout: 8K max, GPT-5: 16K, etc.).
  //   We clamp here per known provider so the request succeeds even
  //   when our preferred ceiling exceeds the provider's actual max.
  //
  // - Regular chat turns (no workspace) keep 4K — answers rarely need
  //   more, and going higher just inflates cost on chatty providers.
  const PROVIDER_MAX_OUTPUT: Record<string, number> = {
    groq: 8192,
    google: 8192,
    openai: 16384,
    anthropic: 16384,
    deepseek: 8192,
    kimi: 8192,
  };
  const meta = MODELS_META[modelId];
  const providerCap = meta ? PROVIDER_MAX_OUTPUT[meta.provider] : undefined;
  // Plain chat raised from 4096 → 16384: artifacts (React apps, docs) routinely
  // exceed 4096 output tokens and were truncating mid-string ("unterminated
  // string literal" on bundle). The cap is a ceiling, not a target — short
  // replies are unaffected. Workspace builds keep the larger 32768.
  const desired = workspaceId ? 32768 : 16384;
  const maxOutputTokens = providerCap ? Math.min(desired, providerCap) : desired;

  // eslint-disable-next-line no-console
  console.log(
    `[chat] streamText start: system=${finalSystem.length}ch, maxOut=${maxOutputTokens}`,
  );

  // MCP tools — plain chats only (workspace turns build code, not call tools).
  // A broken/slow server is skipped (loadMcpTools is per-server try/catch with a
  // connect timeout) so a connector can never break chat.
  let mcpClose: (() => Promise<void>) | null = null;
  let mcpTools: ToolSet = {};
  if (!workspaceId) {
    try {
      const servers = await listMcpServers();
      if (servers.some((s) => s.enabled && s.url)) {
        const loaded = await loadMcpTools(servers);
        mcpTools = loaded.tools;
        mcpClose = loaded.close;
      }
    } catch (err) {
      console.error("[chat] mcp load failed:", err);
    }
  }

  // Image generation — a tool the chat model delegates to, decoupled from the
  // text model: any model that can call tools (incl. DeepSeek, which has no
  // image endpoint of its own) can trigger it; the pixels come from the user's
  // configured image model. Plain chats only, and only when an image provider
  // key exists, so the model never reaches for a dead tool. Generated images
  // are collected here so onFinish can append a markdown reference to the
  // persisted assistant text — tool parts themselves aren't persisted (see
  // persist.ts), the live UI renders them from the streamed tool part.
  const generatedImages: GeneratedImage[] = [];
  let imageTools: ToolSet = {};
  if (!workspaceId && (await imageGenAvailable())) {
    imageTools = {
      generateImage: createGenerateImageTool({
        imageModelId: settings.defaults.imageModel,
        onGenerated: (img) => generatedImages.push(img),
      }),
    };
  }

  // Clear image request → generate the image SERVER-SIDE and return it
  // directly, bypassing the model's tool-calling. Why: deepseek-v4-flash (and
  // other thinking models) can't be forced via tool_choice ("Thinking mode does
  // not support this tool_choice", 400) and, in a long conversation, unreliably
  // answer with TEXT (a hallucinated /api/media url, or a copied placeholder)
  // instead of calling the tool — which renders as a broken image. Doing it
  // here is deterministic: every clear "generate an image of X" yields a real,
  // file-backed image regardless of model. We emulate a tool-output event so
  // the client renders it through its normal path. On failure we fall through
  // to the model (so it can explain, e.g. a missing key).
  // Trigger when the user flips the explicit Image toggle (deterministic, like
  // Open WebUI's image mode) OR when the message clearly reads as an image
  // request (auto-detect convenience). The toggle is the reliable path for
  // thinking models; auto-detect covers "generate a boat" without toggling.
  const forceImage =
    "generateImage" in imageTools &&
    (image === true || hasImageIntent(firstUserText));
  if (forceImage) {
    try {
      const image = await generateChatImage({
        prompt: firstUserText,
        modelId: settings.defaults.imageModel,
      });
      const assistantText = appendImageMarkdown("", [image]);
      if (conversation) {
        try {
          await saveAssistantMessage({
            conversationId: conversation.id,
            text: assistantText,
            tokensIn: 0,
            tokensOut: 0,
            stopReason: "stop",
          });
        } catch (err) {
          console.error("[chat] failed to persist forced-image message", err);
        }
      }
      const encoder = new TextEncoder();
      const headers: Record<string, string> = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-store",
      };
      if (conversation) headers["X-Conversation-Id"] = conversation.id;
      const toolEvent = {
        type: "tool-output-available",
        toolCallId: "forced-image",
        output: {
          status: "ok",
          url: image.url,
          prompt: image.prompt,
          model: image.modelLabel,
        },
      };
      console.log("[chat] image intent → generated server-side, bypassed model");
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(
            encoder.encode(`data: ${JSON.stringify(toolEvent)}\n\n`),
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
    } catch (err) {
      console.warn(
        "[chat] forced image generation failed; falling back to model:",
        err instanceof Error ? err.message : err,
      );
      // fall through to normal streamText
    }
  }

  const result = streamText({
    model: selected.model,
    system: finalSystem,
    messages: modelMessages,
    temperature: settings.defaults.temperature ?? 0.7,
    maxOutputTokens,
    // INTENTIONALLY no abortSignal: req.signal here. If we tied the
    // model run to the request signal, navigating away from the
    // workspace mid-stream would close the SSE connection, fire
    // req.signal.abort(), kill streamText, and onFinish would never
    // run — meaning the assistant message would NEVER persist to
    // the DB. The user would come back to find nothing. By
    // detaching the model run from the request lifecycle (and
    // pairing with consumeStream below), the model finishes
    // server-side regardless of client connection state, onFinish
    // fires, and the message is in the DB when the user returns.
    // Workspace-scoped turns (design AND workbench) shouldn't have
    // webSearch enabled. Models have been calling it instead of
    // producing the requested artifact (`finish: tool-calls`, no
    // boltActions emitted). Workspace turns are for building code, not
    // research. webSearch stays available only for plain chats.
    // Search toggle (DeepSeek) off → drop webSearch; otherwise keep the existing
    // duck-duck-scrape tool. Other providers send no flag → kept (unchanged).
    tools: workspaceId
      ? undefined
      : {
          ...(search === false ? {} : { webSearch: webSearchTool }),
          ...imageTools,
          ...mcpTools,
        },
    stopWhen: stepCountIs(5),
    onError: (event) => {
      // streamText surfaces provider errors via this hook — they don't
      // throw out of the function. An AI SDK error's useful fields
      // (message/statusCode/responseBody) are NON-enumerable, so a bare
      // console.error(err) / JSON.stringify serializes them to `{}` —
      // which is exactly why non-Anthropic provider rejections were
      // undiagnosable. Extract them explicitly.
      const err = (event as { error?: unknown })?.error ?? event;
      const d: Record<string, unknown> = {};
      if (err instanceof Error) {
        const a = err as unknown as Record<string, unknown>;
        d.name = err.name;
        d.message = err.message;
        if (a.statusCode !== undefined) d.statusCode = a.statusCode;
        if (a.responseBody !== undefined) d.responseBody = a.responseBody;
        if (a.url !== undefined) d.url = a.url;
        if (err.cause) d.cause = err.cause instanceof Error ? err.cause.message : err.cause;
      } else {
        d.raw = err;
      }
      console.error(`[chat] streamText error (model=${modelId}):`, JSON.stringify(d));
    },
    onFinish: async ({ text, totalUsage, finishReason }) => {
      // Disconnect MCP servers now that all tool-calling steps are done.
      await mcpClose?.().catch(() => {});
      if (finishReason === "length") {
        // Length cutoff = the model ran out of tokens mid-output. Log it
        // so we can spot patterns; the client surfaces a toast separately.
        console.warn(
          `[chat] finish=length — output truncated at ${totalUsage.outputTokens} tokens (mode=${mode ?? "regular"})`,
        );
      }
      // Fence-recovery fallback: when the model ignored the bolt
      // protocol and dumped code in ```language fenced blocks, write
      // those blocks as files server-side so the user doesn't end up
      // with an empty workspace. The prompt fix should make this rare,
      // but DeepSeek-chat / Llama variants ignore the protocol often
      // enough that having a safety net is worthwhile.
      if (
        workspaceId &&
        !ARTIFACT_RE.test(text) &&
        (FENCE_RE.test(text) || UNCLOSED_FENCE_RE.test(text))
      ) {
        // FENCE_RE has /g flag; reset before recovery uses it again.
        FENCE_RE.lastIndex = 0;
        try {
          const { written, skipped } = await recoverFencesAsFiles(
            workspaceId,
            text,
          );
          if (written > 0) {
            console.warn(
              `[chat] fence-recovery: model emitted ${written} fenced block(s) outside boltArtifact — wrote them as files (${skipped} skipped). Strengthen the prompt or switch to a model that follows the protocol.`,
            );
            // Multi-file landing-page check. When mode=design and the
            // model emitted a single ~30K html file, we know the model
            // ignored both the boltAction protocol AND the multi-file
            // mandate. Surface this loudly so we can spot misbehaving
            // models quickly. The threshold (10KB single file = "fat
            // monolith") is empirical from observing DeepSeek-chat
            // dump everything inline.
            if (mode === "design" && written === 1 && text.length > 10_000) {
              console.warn(
                `[chat] design-mode: model emitted ONE fat ${text.length}-char file instead of the required 8+ component files. Prompt fix needed or route to a stronger model.`,
              );
            }
          }
        } catch (err) {
          console.error("[chat] fence-recovery failed:", err);
        }
      }
      // Persist generated images into the assistant TEXT as markdown so they
      // survive reload — tool parts aren't persisted (persist.ts saves text
      // only). The custom chat client renders the SAME markdown live via the
      // shared helper, so live + reload match byte-for-byte. The markdown <img>
      // loads same-origin, so proxy.ts lets it through even under the bearer gate.
      const assistantText = appendImageMarkdown(text, generatedImages);
      if (!conversation) return;
      try {
        const messageId = await saveAssistantMessage({
          conversationId: conversation.id,
          text: assistantText,
          tokensIn: totalUsage.inputTokens,
          tokensOut: totalUsage.outputTokens,
          stopReason: finishReason,
        });
        // Persist + version any self-contained <jarvisArtifact> blocks the
        // model emitted (plain-chat only; workspace turns use the bolt
        // path). Server-trusted: runs under consumeStream, off the raw
        // persisted text, so artifacts can't be forged by the client.
        if (!workspaceId && process.env.JARVIS_WEB_ARTIFACTS_ENABLED !== "0") {
          // Explicit <jarvisArtifact> tags AND naturally-emitted substantial
          // code/HTML/SVG/mermaid blocks (claude.ai's >15-line heuristic), so
          // the gallery captures everything substantial regardless of tagging.
          const artifacts = [
            ...extractJarvisArtifacts(assistantText),
            ...detectArtifacts(assistantText),
          ];
          if (artifacts.length > 0) {
            await upsertArtifactFromMessage({
              conversationId: conversation.id,
              messageId,
              artifacts,
            });
          }
        }
      } catch (err) {
        console.error("[chat] failed to persist assistant message", err);
      }
    },
  });

  const headers: Record<string, string> = {};
  if (conversation) headers["X-Conversation-Id"] = conversation.id;

  // Drive consumption independently of the response body. Without this,
  // when the client cancels the SSE response (navigating away from the
  // workspace, browser tab close, etc.), the underlying ReadableStream
  // backpressures and the model run stalls. consumeStream keeps pulling
  // chunks server-side so onFinish always runs to completion and the
  // assistant message persists. Fire-and-forget — errors are surfaced
  // via the onError hook on streamText already.
  result.consumeStream();

  return result.toUIMessageStreamResponse({
    headers,
    // Forward per-step usage from streamText into the UI message stream
    // as `messageMetadata` events. The chat client reads these to render
    // a per-turn token + cost chip below each assistant message — same
    // visibility OpenRouter / Cursor / Bolt show. Without this, the
    // user has no insight into how many tokens or dollars a turn cost.
    messageMetadata: ({ part }) => {
      if (part.type === "finish-step") {
        return {
          usage: {
            inputTokens: part.usage.inputTokens ?? 0,
            outputTokens: part.usage.outputTokens ?? 0,
            reasoningTokens:
              part.usage.outputTokenDetails?.reasoningTokens ?? 0,
          },
          model: modelId,
        };
      }
      return undefined;
    },
  });
}
