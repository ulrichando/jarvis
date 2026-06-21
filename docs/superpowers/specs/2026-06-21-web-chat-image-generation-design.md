# Web /chat image generation — design

**Date:** 2026-06-21
**Status:** approved (brainstorming gate passed)
**Scope tree:** `src/web/` only

## Problem

The web app's `/chat` cannot generate images. It is text-only: `streamText`
(AI SDK v6, `ai@6`) with tool-calling already wired (`webSearch` + MCP). The
`Imagen` / "Image generator" sidebar entries are dead placeholder nav stubs —
no backend. We want: ask for an image *in any chat* and get one back, inline.

## Decisions (locked)

- **Surface:** in-chat tool. Image gen happens inside the conversation, not on
  a separate page. The dead `/imagen` page stays out of scope.
- **Default model:** **Nano Banana 2** = `gemini-3.1-flash-image-preview`
  (Gemini Flash Image; fast ~1–3s, cheap). Switchable in Settings.
- **Switchable also-rans** (same backend, exact upstream ids confirmed at
  build time against current provider docs):
  - Nano Banana Pro = `gemini-3-pro-image-preview` (higher fidelity, 2K/4K,
    best in-image text).
  - OpenAI **GPT Image** (`gpt-image-1` family — pin exact current id at build).
  - Google **Imagen 4** (`imagen-4.0-generate-001` / `-ultra-...`).

## Why not DeepSeek (the orchestration insight)

DeepSeek's hosted API (deepseek-chat/-reasoner/V4) is text + image-*understanding*
only — **no image-generation endpoint**. Its only generator, Janus-Pro, is
self-hosted open weights, not on the API. But image gen is **always delegated to
an image model and decoupled from the chat model** — so you keep chatting with
DeepSeek (or any model), it calls the `generateImage` tool, and Nano Banana 2
produces the pixels. Exactly how ChatGPT/Claude do it. Only requirement: the
chat model supports tool-calling (deepseek-chat does); a `/image` intent
fallback covers models that don't.

## Verified API path (the load-bearing fact)

AI SDK v6 exposes **one** call for *every* image model:

```ts
import { generateImage } from "ai";
const { image } = await generateImage({
  model: google.image("gemini-3.1-flash-image-preview"), // or openai.image(...), google.image("imagen-...")
  prompt,
  aspectRatio,           // '1:1' | '16:9' | '9:16' | ...
});
// image.base64 : string, image.mediaType : 'image/png', image.uint8Array : Uint8Array
```

`provider.image(id)` works for Gemini Flash Image, Imagen, and OpenAI GPT Image
alike — no per-model dual path. (Gemini also supports a `generateText` +
`responseModalities:['IMAGE']` → `result.files` path; we do NOT need it.)

## Architecture (6 pieces)

1. **Image backend** — `src/web/src/lib/ai/image.ts` (new)
   - `IMAGE_MODELS` registry: `id → { provider, upstreamId, label, default? }`,
     mirroring the shape of `MODELS_META`.
   - `generateChatImage({ prompt, modelId?, aspectRatio? }) → { base64, mediaType }`.
     Resolves the provider via the **existing** `resolveApiKey(provider)` +
     `buildProvider(...)` from `lib/ai/models.ts` (export those for reuse), builds
     `provider.image(upstreamId)`, calls `generateImage()`.
   - Throws `MissingApiKeyError` (already exists) when the image provider's key
     is absent → reuses the chat route's existing friendly 400 path.
   - `imageGenAvailable()` helper: true iff at least one IMAGE_MODELS provider
     has a key (used to gate the tool).

2. **The tool** — `src/web/src/lib/tools/generate-image.ts` (new)
   - `generateImageTool` via AI SDK `tool({...})` (same shape as `webSearchTool`).
   - Input: `{ prompt: string, aspectRatio?: enum }`.
   - `execute`: read chosen image model from settings → `generateChatImage` →
     persist bytes (piece 5) → return `{ url, mediaType, prompt }`.
   - Description tuned to fire on "generate / draw / create / make an image".

3. **Wire into chat** — `src/web/src/app/api/chat/route.ts` (edit, ~3 lines)
   - Add `generateImage: generateImageTool` to the **plain-chat** `tools` map
     (the `!workspaceId` branch, alongside `webSearch`), gated on
     `imageGenAvailable()`. Workspace/design turns stay tool-free (unchanged).

4. **Render** — `src/web/src/components/chat/message.tsx` (edit)
   - In the assistant branch, when a `tool-generateImage` part carries a `url`,
     render an inline image card: `<img>` + prompt caption + download button +
     a loading shimmer while the tool is `input-available`/running.
   - Reuses the existing `image/*` handling pattern; extends
     `toolTraceFromMessage` materialization to expose the image output.

5. **Storage / media route** — `src/web/src/app/api/media/[id]/route.ts` (new)
   - `execute` writes bytes to a server media dir (e.g. `~/.jarvis/media/<id>.png`)
     and returns `/api/media/<id>` as the URL. Keeps Postgres light (no
     multi-MB base64 per turn) and makes images downloadable.
   - Route streams the file with the right `Content-Type`; same auth posture as
     other `/api/*` routes (bearer/cookie gate via `src/proxy.ts`).
   - Fallback if this proves heavy: inline `data:` URL (renderer already
     supports it) — but disk+URL is the chosen default.

6. **Settings** — settings store + one Settings control (edit)
   - Add `settings.defaults.imageModel` (default `gemini-3.1-flash-image-preview`).
   - Settings dropdown lists only IMAGE_MODELS whose provider key exists.
   - Ships working out-of-the-box; the dropdown is the "switchable" part.

## Scope (regression-prevention rule 1)

- **SCOPE:** new `lib/ai/image.ts`, `lib/tools/generate-image.ts`,
  `app/api/media/[id]/route.ts`; edits to `app/api/chat/route.ts`,
  `components/chat/message.tsx`, settings store + one Settings control; export
  `buildProvider`/`resolveApiKey` from `lib/ai/models.ts`. All under `src/web/`.
- **OUT:** voice-agent, cli, desktop-tauri; the dead `/imagen` page;
  workspace/design image gen; self-hosted Janus-Pro / Ollama local-image rung.
- **WHY OUT:** separate trees / separate surfaces; local image gen is its own
  project.

## Verification

- `cd src/web && npm run build` green (catches type/import errors).
- Unit tests for IMAGE_MODELS resolution + `imageGenAvailable()` key-gating +
  media route content-type.
- Live: ask a chat (incl. deepseek-chat) "generate an image of X" → image
  renders inline + downloads. Confirm the tool is absent when no image key.

## Deferred / future (not this pass)

- Local image rung: self-hosted **Janus-Pro** or an Ollama image model, so
  offline/DeepSeek-style local gen works (parallels the existing local-LLM
  fallback design).
- Composer aspect-ratio / size controls.
- Image **editing** (reference images) — AI SDK v6 supports it; add once
  generation ships.
