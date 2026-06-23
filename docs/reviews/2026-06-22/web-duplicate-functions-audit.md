# Duplicate Functions Report

Generated: 2026-06-22 22:02

> **Scope & method.** Semantic-duplicate audit of `src/web/src/` via the
> `superpowers-lab:finding-duplicate-functions` skill (extract → categorize →
> per-category opus detection). Covered the **629 exported** functions; the 526
> internal/private helpers were excluded per the skill's guidance and remain a
> possible second pass.
>
> **Action status (updated 2026-06-22).**
> - ✅ **RESOLVED — bridge auth-gate cluster** (commit `bc7b610c`): the worker
>   session-token gate (7 routes) → `lib/bridge/authz.ts::authorizeSessionToken`;
>   the MCP mutation gate (2 routes) → `lib/mcp/authz.ts::requireMcpAuth`; the 4
>   `store.ts` credential validators now use a constant-time `secretEquals`.
>   The HIGH groups "Worker session-token auth gate" and "MCP mutation auth gate"
>   below are DONE; the `requireAuth`/IDOR MEDIUM follow-ups went with them.
> - ⏳ **OPEN** — the remaining HIGH clusters: the global-vs-workspace
>   **knowledge/skills stores** (whole-module copy-paste), the web **`use-*`
>   data-hooks** boilerplate, the **Kimi** client built twice, and
>   `lib/bridge/events.ts` self-duplication, plus the MEDIUM/LOW items.

## Summary

| Confidence | Count | Action |
|------------|-------|--------|
| HIGH | 9 | Consolidate immediately |
| MEDIUM | 16 | Investigate further |
| LOW | 3 | Review if time permits |

---

## HIGH Confidence Duplicates

These functions are definitely duplicates. Consolidate them.

### Toggle a knowledge document's enabled/disabled state by flipping its entry in the _meta.json `disabled` list (load meta, sanitize name, add/remove from disabled array, save meta).

**Category:** config

**Functions:**
- `setKnowledgeEnabled` in `src/web/src/lib/knowledge/store.ts:131` - Global knowledge store (~/.jarvis/knowledge/). Signature (name, enabled). loadMeta()/saveMeta() take no args (fixed dir).
- `setKnowledgeEnabled` in `src/web/src/lib/workspace/knowledge.ts:152` - Per-workspace knowledge store (<workspaceRoot>/.jarvis/knowledge/). Signature (workspaceId, name, enabled). loadMeta(workspaceId)/saveMeta(workspaceId, meta) thread the workspace id to locate the dir. Body otherwise byte-identical.

**Differences:** Only the workspaceId parameter that selects the meta-file root. The enable/disable logic (safeName guard, wasDisabled check, the three-branch add/remove/already-in-state, save) is identical. The global version is a strict special case of the workspace version with a hardcoded root. The two ENTIRE files are near-duplicates (loadMeta, saveMeta, safeName, listKnowledge, addKnowledge, removeKnowledge, setKnowledgeEnabled, read-block all mirror each other), so this pair is the representative of a whole-module duplication.

**Recommendation:** Keep `src/web/src/lib/workspace/knowledge.ts:setKnowledgeEnabled` - Semantically identical — a single parametrized knowledge store (root resolved from an optional workspaceId, defaulting to ~/.jarvis/knowledge for the global case) would collapse both files. INVESTIGATE rather than blind CONSOLIDATE because the two stores have different public surfaces wired into different API routes (global vs workspace-scoped) and slightly different prompt-block wording/heading ('## Knowledge' vs '## Workspace knowledge'); the consolidation is a small refactor of the shared store module, best done deliberately across both files at once, not just this one function.

---

### Read all enabled knowledge documents from a directory, truncate each to a char cap, and concatenate them into a single system-prompt context block (empty string when nothing is enabled).

**Category:** data-transform

**Functions:**
- `readGlobalKnowledgeBlock` in `src/web/src/lib/knowledge/store.ts:154` - Global scope (no workspaceId). listKnowledge() -> filter enabled -> read each from KNOWLEDGE_DIR -> truncate to MAX_INJECT_CHARS with '…[truncated]' -> parts.push(`### ${name}\n${trimmed}`) -> wrap in '## Knowledge' header. Identical control flow and try/catch skip-on-missing.
- `readKnowledgeBlock` in `src/web/src/lib/workspace/knowledge.ts:178` - Workspace-scoped (takes workspaceId). listKnowledge(workspaceId) -> filter enabled -> read each from knowledgeRoot(workspaceId) -> truncate to hardcoded 4096 with '…[truncated]' -> parts.push(`### ${name}\n${trimmed}`) -> wrap in '## Workspace knowledge' header. Same structure as the global variant.

**Differences:** Scope (global KNOWLEDGE_DIR vs per-workspace knowledgeRoot(workspaceId)); truncation cap (named MAX_INJECT_CHARS vs hardcoded 4096); and the wrapper heading/lead-in text ('## Knowledge' vs '## Workspace knowledge'). The read+truncate+concat loop is otherwise line-for-line identical.

**Recommendation:** Keep `src/web/src/lib/workspace/knowledge.ts:readKnowledgeBlock` - Extract one helper `readKnowledgeBlock({ dir, maxChars, heading, lead })` (or pass listKnowledge + root + cap as params) and have both call sites supply their dir/cap/header. The bodies are duplicated apart from three small parameters; a shared formatter removes the drift risk (e.g. the 4096 vs MAX_INJECT_CHARS divergence).

---

### Full CRUD store for plaintext 'knowledge' documents on disk: list (stat+enabled flag, sorted by mtime), add (safeName + empty/1MB/50-doc-cap validation, write, return doc), remove (unlink + clear from disabled meta), plus the private safeName/loadMeta/saveMeta helpers and a system-prompt concatenation block. Same KnowledgeDoc type, same MAX_FILE_BYTES (1MB) / MAX_TOTAL_DOCS (50), same per-doc 4K inject truncation.

**Category:** file-ops

**Functions:**
- `listKnowledge` in `src/web/src/lib/knowledge/store.ts:54` - Global store: fixed dir ~/.jarvis/knowledge. readdir withFileTypes, skip _meta.json/dotfiles, stat each, enabled = !meta.disabled.includes(name), sort by updatedAt desc.
- `listKnowledge` in `src/web/src/lib/workspace/knowledge.ts:67` - Workspace-scoped: same body, only difference is root = knowledgeRoot(workspaceId) under workspaceRoot(id) and loadMeta takes workspaceId. Otherwise line-for-line identical to the global version.
- `addKnowledge` in `src/web/src/lib/knowledge/store.ts:82` - Global: safeName -> empty check -> 1MB byteLength check -> 50-doc cap via listKnowledge -> mkdir+writeFile -> stat -> return doc. Identical validation ladder + error strings to the workspace twin.
- `addKnowledge` in `src/web/src/lib/workspace/knowledge.ts:100` - Workspace: same validation ladder + identical error strings ('invalid name'/'empty content'/'file too large (max 1MB)'/'cap reached (max 50 docs)'); only adds workspaceId threading + knowledgeRoot(workspaceId).
- `removeKnowledge` in `src/web/src/lib/knowledge/store.ts:115` - Global: safeName -> unlink -> if name in meta.disabled, filter it out + saveMeta -> return true; catch -> false.
- `removeKnowledge` in `src/web/src/lib/workspace/knowledge.ts:132` - Workspace: identical logic (unlink + prune disabled list) with workspaceId threaded through unlink path and loadMeta/saveMeta.

**Differences:** Only scope/path resolution: the global store hardcodes ~/.jarvis/knowledge and its loadMeta/saveMeta take no args; the workspace store derives the dir from workspaceRoot(workspaceId) and threads workspaceId through loadMeta/saveMeta. Type, size/count caps, safeName regex, validation order, error strings, sort, and the readGlobalKnowledgeBlock/readKnowledgeBlock prompt builder (also mirrored, differs only in the heading text 'Knowledge' vs 'Workspace knowledge') are all identical. (setKnowledgeEnabled — not in this category list — is also an exact mirror.)

**Recommendation:** Keep `src/web/src/lib/workspace/knowledge.ts` - These two files are a copy-paste pair. Extract a single parameterized knowledge-store factory that takes a 'root resolver' (() => dir for global, (id) => dir for workspace) and returns the CRUD set; the global store becomes the factory bound to the fixed dir, the workspace store the factory bound to workspaceRoot. Keeping both invites drift (e.g. the inject-truncation constant already drifted: global names it MAX_INJECT_CHARS=4096, workspace inlines a literal 4096).

---

### Worker session-token auth gate: extract the bearer from the Authorization header (401 'Missing bearer' if absent), then validateSessionToken(store, sessionId, token) (401 'Invalid session token' if invalid), else allow the request

**Category:** http-api

**Functions:**
- `authorize` in `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/worker/route.ts:17` - Named sync helper `function authorize(req, sessionId): NextResponse | null`. Body: extractBearer → bridgeError(401,'Missing bearer') → validateSessionToken(getStore(),...) → bridgeError(401,'Invalid session token') → null. Used by PUT + GET.
- `authorize` in `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/worker/internal-events/route.ts:16` - Named sync helper, byte-identical to worker/route.ts's authorize (same signature, same 4 lines). Used by POST + GET.
- `POST` in `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/worker/events/route.ts:32` - Same gate INLINED in the handler (lines 37-49): extractBearer → 'Missing bearer' 401 → validateSessionToken(store,...) → 'Invalid session token' 401.
- `GET` in `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/worker/events/stream/route.ts:25` - Same gate inlined (lines 30-34) before the SSE setup.
- `POST` in `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/worker/events/delivery/route.ts:11` - Same gate inlined (lines 16-20).
- `POST` in `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/worker/heartbeat/route.ts:10` - Same gate inlined (lines 15-23).
- `POST` in `src/web/src/app/api/bridge/v1/code/sessions/[sessionId]/worker/register/route.ts:11` - Same gate inlined (lines 16-21).

**Differences:** The two named `authorize()` helpers are byte-identical (one is sync-non-async, one is sync; both NextResponse|null). The other five inline the exact same 4-line check directly in the handler. Only difference is named-helper vs inlined.

**Recommendation:** Keep `src/web/src/lib/bridge/authz.ts:authorizeSessionToken (new export)` - Extract a single `authorizeSessionToken(req, sessionId): NextResponse | null` next to the existing authorizeSession in lib/bridge/authz.ts and have all 7 worker routes call it. The pattern is already centralized for the cookie-based gate (authorizeSession); the token-based gate is the only worker-auth variant and is repeated 7x. Pure de-dup, no behavior change.

---

### MCP mutation auth gate: allow if JARVIS_AUTH_DISABLED=1, allow if a bearer token is present (CLI), else resolve getUserId and reject with 401 when it is the LOCAL_USER_ID fallback (login gate active)

**Category:** http-api

**Functions:**
- `requireAuth` in `src/web/src/app/api/mcp/route.ts:21` - Used by POST/PATCH/DELETE on /api/mcp. Imports getUserId, LOCAL_USER_ID, extractBearer identically.
- `requireAuth` in `src/web/src/app/api/mcp/oauth/start/route.ts:16` - Byte-for-byte identical body to api/mcp/route.ts::requireAuth. Header comment literally says 'Same mutation gate as /api/mcp'. Used by POST.

**Differences:** identical

**Recommendation:** Keep `src/web/src/lib/mcp (new shared requireMcpAuth helper) — or src/web/src/lib/auth-helpers.ts` - Two byte-identical copies of the same MCP-mutation auth gate; the second file's own comment admits it duplicates the first. Extract one exported helper and import it in both route files.

---

### Construct an AI-SDK provider/model client for the Kimi (Moonshot) provider: create an OpenAI-compatible factory with name "kimi" + resolved apiKey/baseURL (default https://api.moonshot.ai/v1) and bind the kimi-k2.6 model.

**Category:** provider-impl

**Functions:**
- `buildKimiClient` in `src/web/src/lib/ai/kimi/shared.ts:55` - resolveApiKey("kimi") -> createOpenAICompatible({name:"kimi", apiKey, baseURL: baseURL ?? "https://api.moonshot.ai/v1"}) -> factory("kimi-k2.6"). Also throws KimiKeyMissingError on no key and returns the raw apiKey + baseURL (the K2.6 mode handlers in instant/thinking/agent/swarm.ts use those for direct fetch streaming, not just the LanguageModel).
- `buildProvider` in `src/web/src/lib/ai/models.ts:84` - The `case "kimi":` branch is byte-for-byte the same client construction: createOpenAICompatible({name:"kimi", apiKey, baseURL: baseURL ?? "https://api.moonshot.ai/v1"}). buildProvider is the generic multi-provider factory; the kimi branch is the exact core that buildKimiClient re-implements. The hardcoded KIMI_BASE_URL constant in shared.ts duplicates the literal default already in this branch.
- `getModel` in `src/web/src/lib/ai/models.ts:132` - getModel("kimi-k2-*") resolves via MODEL_IDS -> {provider:"kimi", modelId:"kimi-k2.6"}, then resolveApiKey("kimi") -> buildProvider("kimi", apiKey, baseURL) -> factory("kimi-k2.6"). This is the same key-resolve + build + bind-kimi-k2.6 sequence buildKimiClient performs by hand; getModel just doesn't surface the raw apiKey/baseURL the Kimi handlers also want.

**Differences:** buildProvider is the shared generic factory (returns an unbound provider factory). buildKimiClient additionally (a) resolves the API key, (b) throws KimiKeyMissingError vs buildProvider's caller throwing MissingApiKeyError, (c) binds the kimi-k2.6 model id, and (d) returns the raw apiKey + baseURL so the K2.6 mode handlers can do direct fetch-based streaming. getModel does (a)+(c)+the build but discards apiKey/baseURL. So buildKimiClient = getModel("kimi-k2-*") + apiKey/baseURL passthrough + a Kimi-flavored error class — a hand-rolled specialization rather than genuinely distinct behavior.

**Recommendation:** Keep `src/web/src/lib/ai/models.ts:buildProvider` - buildKimiClient should not re-implement the Kimi client; it should delegate the provider construction to buildProvider("kimi", apiKey, baseURL) (or wrap getModel) so the Moonshot base-URL default + createOpenAICompatible config live in exactly one place. Keep a thin buildKimiClient wrapper that adds the apiKey/baseURL passthrough + KimiKeyMissingError + kimi-k2.6 binding the mode handlers depend on — but its inner createOpenAICompatible call and the duplicated KIMI_BASE_URL literal should be removed in favor of the shared kimi branch. The drift risk is concrete: the Moonshot base URL is currently hardcoded in three spots (buildProvider kimi case, buildProvider's MODEL_IDS comment is fine, and shared.ts KIMI_BASE_URL).

---

### Return a Promise that resolves true when a named event fires on the shared EventEmitter bus, or false after a timeout, always unsubscribing the one-shot listener on completion.

**Category:** small-utils

**Functions:**
- `waitForWork` in `src/web/src/lib/bridge/events.ts:19` - Waits on `work-available:${envId}` via eventName(envId). new Promise + done-guard + cleanup(clearTimeout + bus.off) + bus.once + setTimeout(timeoutMs).
- `waitForInbound` in `src/web/src/lib/bridge/events.ts:50` - Waits on `inbound:${sessionId}` via inboundEventName(sessionId). Body is a character-for-character copy of waitForWork aside from the event-name resolver and the docstring ("Same contract as waitForWork" is stated inline).

**Differences:** Only the event-name function (eventName(envId) vs inboundEventName(sessionId)) and the docstring differ. The promise machinery (done flag, cleanup, bus.once, setTimeout) is identical.

**Recommendation:** Keep `src/web/src/lib/bridge/events.ts:waitForWork` - Extract a private waitForEvent(eventKey: string, timeoutMs: number): Promise<boolean> and have both waitForWork/waitForInbound call it with their resolved key. Same file, identical contract; the duplication is pure copy-paste and the docstring already admits it ('Same contract as waitForWork').

---

### List/read data hook: useQuery wrapping `await fetch(url) -> if(!res.ok) throw -> return parsed JSON` with a string queryKey and optional staleTime

**Category:** ui-helpers

**Functions:**
- `useKnowledge` in `src/web/src/hooks/use-knowledge.ts:12` - useQuery(['knowledge']); fetch('/api/knowledge'); !res.ok throw 'Failed to load knowledge'; return (await res.json()).docs
- `useSkills` in `src/web/src/hooks/use-skills.ts:12` - useQuery(['skills'], staleTime 10_000); fetch('/api/skills'); !res.ok throw 'Failed to load skills'; return (await res.json()).skills
- `useUsage` in `src/web/src/hooks/use-usage.ts:22` - useQuery(['usage'], staleTime 30_000); fetch('/api/usage'); !res.ok throw 'Failed to load usage'; return res.json(). Identical shape, just no .field unwrap

**Differences:** Only the URL, queryKey, staleTime, and the JSON sub-field unwrapped (docs/skills/none) differ. The fetch-or-throw-or-parse body is otherwise identical.

**Recommendation:** Keep `new shared helper, e.g. src/web/src/hooks/use-resource.ts::useResourceQuery(key, url, {field, staleTime})` - Three byte-for-byte-equivalent list hooks (the prompt's named prime suspects). A tiny generic `useResourceQuery` (or reusing the existing `fetchJson` helper already defined in use-conversations/use-projects/use-settings) removes the repeated boilerplate. Low risk — all three callers want identical behavior.

---

### Look up a stored bridge credential by id (environment/session/work) and return whether a supplied secret/token matches it exactly (constant-shape equality check returning boolean).

**Category:** validation

**Functions:**
- `validateEnvSecret` in `src/web/src/lib/bridge/store.ts:470` - findEnvironment(store,envId); return env.environment_secret === secret. false if env missing.
- `validateGitCapToken` in `src/web/src/lib/bridge/store.ts:839` - parseContainerMeta(findSession(...)); return !!m.gitCapToken && m.gitCapToken === token.
- `validateSessionToken` in `src/web/src/lib/bridge/store.ts:1125` - findSession(store,sessionId); return !!row && !!row.session_token && row.session_token === token.
- `validateWorkSessionToken` in `src/web/src/lib/bridge/store.ts:1176` - SQL JOIN work→sessions to fetch s.session_token by (workId,envId); return !!row?.t && row.t === token. Compares the SAME session_token as validateSessionToken but reached via a work row instead of a session id.

**Differences:** All four are 'fetch stored credential, compare === supplied, return bool' with a null/empty guard. They differ only in WHICH row/field they read: environment_secret, container-meta gitCapToken, session_token (by sessionId), and session_token (by workId via JOIN). validateWorkSessionToken and validateSessionToken validate the identical credential (session_token) reached through different keys.

**Recommendation:** Keep `src/web/src/lib/bridge/store.ts:validateSessionToken` - The four share a single 'compare-stored-credential' shape and could route through one private helper compareSecret(stored, supplied) (handles the null/empty + equality, ideally timing-safe). Each public function would keep its own lookup and call the helper. validateWorkSessionToken/validateSessionToken are the closest pair (same session_token). Keep separate public signatures (callers pass different keys); consolidate only the comparison core. Worth a security note: equality is plain ===, not constant-time.

---


## MEDIUM Confidence Duplicates

These functions likely do the same thing. Investigate before consolidating.

### Toggle a boolean flag column (0/1) on a single sessions row by session_id

**Category:** database

**Functions:**
- `setSessionAutofix` in `src/web/src/lib/bridge/store.ts:970` - UPDATE sessions SET autofix = ? WHERE session_id = ?  (on ? 1 : 0)
- `setSessionAutomerge` in `src/web/src/lib/bridge/store.ts:991` - UPDATE sessions SET automerge = ? WHERE session_id = ?  (on ? 1 : 0)
- `setSessionPinned` in `src/web/src/lib/bridge/store.ts:1326` - UPDATE sessions SET pinned = ? WHERE session_id = ?  (pinned ? 1 : 0)
- `setSessionRead` in `src/web/src/lib/bridge/store.ts:1337` - UPDATE sessions SET read = ? WHERE session_id = ?  (read ? 1 : 0)

**Differences:** Only the column literal (autofix / automerge / pinned / read) differs; statement shape, params, and 0/1 coercion are byte-identical. All four hit the same `sessions` table keyed by session_id.

**Recommendation:** INVESTIGATE - Mechanically these are one operation parameterized by column. A `setSessionFlag(store, sessionId, col, on)` private helper could back all four while keeping the public named wrappers (col is a hardcoded literal, never user input, so no injection risk). Borderline because each is a distinct, self-documenting user-facing toggle — collapsing the public names would hurt readability, so the win is a 1-line shared body, not fewer exports. Cheap, low-risk dedup; do it only if these grow further.

---

### List all non-archived sessions that have a given boolean flag set (background-tick scan targets)

**Category:** database

**Functions:**
- `listAutofixSessions` in `src/web/src/lib/bridge/store.ts:984` - SELECT * FROM sessions WHERE autofix = 1 AND archived = 0
- `listAutomergeSessions` in `src/web/src/lib/bridge/store.ts:998` - SELECT * FROM sessions WHERE automerge = 1 AND archived = 0

**Differences:** Only the flag column (autofix vs automerge) differs; both return SessionRow[] from `sessions` with `<flag> = 1 AND archived = 0`. Identical otherwise.

**Recommendation:** INVESTIGATE - Same SELECT shape parameterized by a hardcoded column literal. Could share one `listSessionsWithFlag(store, col)` body behind the two named wrappers. Same judgment call as the flag setters: tiny shared body, distinct call sites stay named. Low priority.

---

### Emit a wake-up event on a shared Node EventEmitter bus for a single keyed channel (per-env work-available vs per-session inbound), to wake a poll/SSE loop waiting on that key.

**Category:** event-handling

**Functions:**
- `emitWorkAvailable` in `src/web/src/lib/bridge/events.ts:10` - bus.emit(eventName(envId)); eventName = `work-available:${envId}`. Paired with waitForWork (same file, line 19).
- `emitInbound` in `src/web/src/lib/bridge/events.ts:40` - bus.emit(inboundEventName(sessionId)); inboundEventName = `inbound:${sessionId}`. Paired with waitForInbound (same file, line 50), whose docstring literally says 'Same contract as waitForWork.'

**Differences:** Identical body modulo the channel-name helper (`work-available:` vs `inbound:` prefix) and the param label (envId vs sessionId). Both are one-line `bus.emit(<channelName>(id))`. Their waiter counterparts (waitForWork / waitForInbound) are also byte-for-byte identical except the same channel name — the whole emit+wait pair is one parameterizable pub/sub channel duplicated twice.

**Recommendation:** INVESTIGATE - Same-file, deliberately-paired wrappers over one EventEmitter. Could collapse to a generic `emitChannel(prefix, id)` + `waitForChannel(prefix, id, timeoutMs)` (the waiters are the real duplication; the emitters are trivial one-liners over them). But the two named-channel call sites read clearly and the duplication is tiny — consolidate only if a 3rd channel appears, else the named wrappers are fine as the public API. Low payoff; not HIGH because the cost is ~4 trivial lines and the names aid call-site readability.

---

### Full CRUD store for on-disk markdown 'skill' definitions invoked as /name: list (readdir *.md, parse description/frontmatter, stat mtime, sort by name), create-or-update (safeName + body/size/cap validation, write file, return skill), delete (unlink <name>.md).

**Category:** file-ops

**Functions:**
- `listSkills` in `src/web/src/lib/skills/store.ts:47` - Global: dir ~/.jarvis/skills; filter *.md non-dot; for each read+stat, parse via DESC_RE '<!-- description: ... -->' first-line comment; sort by name. Returns {name,body,description,updatedAt}.
- `listSkills` in `src/web/src/lib/workspace/skills.ts:77` - Workspace: dir under workspaceRoot(id); filter *.md; read+stat; parse via YAML frontmatter (parseFrontmatter) yielding name/description/kind; sort by name. Returns extra fields {kind,bytes}.
- `addSkill` in `src/web/src/lib/skills/store.ts:79` - Global: safeName -> non-empty body -> 256KB cap -> 100-skill cap -> write '<!-- description -->\n<body>' -> return. No 'kind'.
- `saveSkill` in `src/web/src/lib/workspace/skills.ts:110` - Workspace: safeName -> validate kind in {prompt,shell} -> non-empty body -> buildFile (YAML frontmatter) -> 64KB cap -> write -> return. Named saveSkill (upsert) vs addSkill but same intent.
- `removeSkill` in `src/web/src/lib/skills/store.ts:109` - Global: safeName -> unlink path.join(SKILLS_DIR, `${safe}.md`) -> true; catch false.
- `deleteSkill` in `src/web/src/lib/workspace/skills.ts:154` - Workspace: safeName -> unlink path.join(skillsRoot(id), `${safe}.md`) -> true; catch false. removeSkill vs deleteSkill, structurally identical save for the root.

**Differences:** Same purpose and the same readdir/unlink/sort skeleton, but the on-disk file FORMAT and the Skill schema genuinely differ: global uses an HTML-comment '<!-- description: -->' header + prompt-only Skill {name,body,description,updatedAt}, 256KB/100-skill caps, name regex /^[a-z0-9][a-z0-9_-]{0,63}$/; workspace uses YAML frontmatter with a kind:'prompt'|'shell' field + Skill {name,description,kind,body,bytes,updatedAt}, 64KB cap, name regex /^[a-z][a-z0-9-]*$/ (<=60). delete twins (removeSkill/deleteSkill) ARE near-identical; the list/add twins share structure but diverge in parse/serialize + schema.

**Recommendation:** INVESTIGATE - Same role (a /name skill store) but not a drop-in merge: the two formats (HTML-comment vs YAML frontmatter) and Skill shapes (no-kind vs kind) differ, and the per-store caps/regex differ. Worth unifying on the richer YAML+kind format behind one parameterized store (root resolver + parse/serialize), but only after confirming both UIs/routes can adopt the kind field and the larger/smaller byte caps are reconciled — not a mechanical rename. The remove/delete twins can be merged immediately.

---

### Bridge session-ownership (IDOR) gate: pass if a bearer is present (CLI worker); else look up the session, 404 if missing, find its owning environment, resolve getUserId, and 403 'Not your session' when the env is owned by a different user

**Category:** http-api

**Functions:**
- `authorizeSession` in `src/web/src/lib/bridge/authz.ts:20` - The ALREADY-EXTRACTED shared version. Adds a 401-vs-403 nuance: a LOCAL_USER_ID fallback against a real-account-owned session returns 401 'Session expired' instead of 403. Used by diff/plan/pins(GET via?)/pr-status routes.
- `authorizeMutation` in `src/web/src/app/api/bridge/v1/sessions/[sessionId]/route.ts:25` - Inline copy of the same gate, minus the 401 lapsed-session nuance. Used by PATCH/DELETE.
- `authorizeMutation` in `src/web/src/app/api/bridge/v1/sessions/[sessionId]/pr/route.ts:16` - Inline copy, identical body to sessions/route.ts::authorizeMutation.
- `authorizeSession` in `src/web/src/app/api/bridge/v1/sessions/[sessionId]/pins/route.ts:18` - Inline copy; header comment says 'Mirrors sessions/[sessionId]/route.ts'. Same body as the shared authz.ts version minus the 401 nuance.

**Differences:** All four share the same find-session → find-env → getUserId → 403-on-mismatch core. The shared lib/bridge/authz.ts version additionally returns 401 'Session expired' for the LOCAL_USER_ID-vs-real-account case; the three inline copies return a plain 403 there. Otherwise identical.

**Recommendation:** CONSOLIDATE - A shared authorizeSession already exists and is used by several sibling routes (diff/plan/pr-status). The three inline copies (authorizeMutation x2, authorizeSession x1) should call it instead — adopting its 401 lapsed-session nuance is a strict improvement. NOT the env-scoped authorize/authorizeWrite (config route) or authorizeRoutine, which gate different resources.

---

### Kimi mode-handler prologue: build the Kimi client and, on a KimiKeyMissingError (checked via instanceof AND duck-typed err.name to survive vi.mock boundary), return a 401 text/event-stream Response carrying a {type:'kimi-error',status:401,message:'Kimi API key missing or invalid'} SSE frame + [DONE]

**Category:** http-api

**Functions:**
- `handleInstant` in `src/web/src/lib/ai/kimi/instant.ts:13` - Lines 14-32: the buildKimiClient try/catch + KimiKeyMissingError → 401 SSE block, then formatKimiError(err) fallback.
- `handleThinking` in `src/web/src/lib/ai/kimi/thinking.ts:44` - Same prologue; comment at line 49 explicitly says 'Match the Instant handler's KimiKeyMissingError handling — both instanceof and duck-typed name check'.
- `handleAgent` in `src/web/src/lib/ai/kimi/agent.ts:14` - Same buildKimiClient + KimiKeyMissingError → 401 SSE prologue copied verbatim.
- `handleSwarm` in `src/web/src/lib/ai/kimi/swarm.ts:52` - Same prologue (lines 53-?) before the swarm-specific body.

**Differences:** Only the prologue (client build + key-missing 401 SSE response) is duplicated; the bodies after diverge (instant disables thinking, thinking enables extended reasoning, agent/swarm run multi-agent flows). shared.ts already exports buildKimiClient + formatKimiError + KimiKeyMissingError but NOT the key-missing-guard wrapper, so each handler re-implements it.

**Recommendation:** CONSOLIDATE - Extract the build-client-or-return-401-SSE prologue into shared.ts (the module that already houses buildKimiClient/formatKimiError). Each of the 4 handlers then does `const client = await guardKimiClient(); if (client instanceof Response) return client;`. Removes the verbatim copy the thinking.ts comment is apologizing for. Handler bodies stay separate (genuinely different modes).

---

### Ensure a /code bridge session has an ingress token: read the session's existing session_token, and if absent mint a fresh `sit_<base64url-24-bytes>` token and persist it via setSessionToken.

**Category:** session-management

**Functions:**
- `launchContainerSession` in `src/web/src/lib/bridge/containers.ts:184` - Lines 227-232: `let token = session?.session_token ?? null; if (!token) { token = `sit_${randomBytes(24).toString('base64url')}`; setSessionToken(store, sessionId, token); }`. Token-mint is one step inside a much larger container-launch routine.
- `dispatchSessionWork` in `src/web/src/lib/bridge/dispatch.ts:25` - Lines 31-36: `let sessionToken = existing?.session_token ?? null; if (!sessionToken) { sessionToken = `sit_${randomBytes(24).toString('base64url')}`; setSessionToken(store, sessionId, sessionToken); }`. Byte-for-byte the same mint-if-absent logic; the rest of the function builds a CCR-v2 work secret and enqueues work.

**Differences:** The token-mint fragment itself is identical (same prefix, same 24-byte base64url, same findSession→setSessionToken). The enclosing functions differ entirely: launchContainerSession also bumps the worker epoch, clones the repo, configures the egress proxy, etc.; dispatchSessionWork builds and enqueues a work secret. Only the inline token-ensure step is duplicated, not the whole function.

**Recommendation:** INVESTIGATE - Both call sites already import findSession + setSessionToken from ./store. Extract a tiny `ensureSessionToken(store, sessionId): string` helper into store.ts that returns the existing token or mints+persists `sit_<base64url>` and have both call it. Single source for the token format means a future rotation/format change can't drift between the two paths. Low value (one shared 4-line fragment, not a duplicated function), so INVESTIGATE rather than urgent CONSOLIDATE.

---

### Mint a random opaque secret/token via node:crypto (randomBytes 32 -> base64url) and persist it, returning the existing value if one is already stored (get-or-create).

**Category:** small-utils

**Functions:**
- `getOrCreateProxyJwtSecret` in `src/web/src/lib/bridge/proxySecret.ts:62` - Storage: ~/.jarvis/keys.env (env override wins). Mint primitive: randomBytes(32).toString('base64url'). Returns raw secret string.
- `getOrCreateBridgeToken` in `src/web/src/lib/bridge/store.ts:299` - Storage: SQLite bridge_tokens table keyed by userId. Mint: `jbr_${randomBytes(24).toString('base64url')}` (24 bytes + 'jbr_' prefix). Sibling helper genSecret() at store.ts:327 IS exactly randomBytes(32).toString('base64url') — byte-identical to getOrCreateProxyJwtSecret's mint line.
- `setShareToken` in `src/web/src/lib/workspace/storage.ts:414` - Storage: workspaces _meta.json (lock-protected). Mint: randomUUID().replace(/-/g, '') (UUID hex, not randomBytes). Adds createdAt/expiresAt TTL envelope.

**Differences:** Same get-or-create-a-random-credential intent, but each is bound to a DIFFERENT persistence backend (keys.env file / SQLite / workspace meta JSON) and emits a DIFFERENT token shape (bare base64url-32 / 'jbr_'+base64url-24 / hex-UUID). The get-or-create wrappers are NOT interchangeable; only the underlying random-mint primitive (randomBytes(N).toString('base64url')) is duplicated — and it is byte-identical between getOrCreateProxyJwtSecret and store.ts's local genSecret().

**Recommendation:** INVESTIGATE - Don't merge the get-or-create wrappers — different stores/formats by design. DO extract the shared random-credential primitive (e.g. randomToken(bytes=32) -> base64url, and an optional prefixed variant) into one util so getOrCreateProxyJwtSecret and store.ts genSecret/getOrCreateBridgeToken stop re-declaring randomBytes(...).toString('base64url'). Low-risk dedupe of the generator only.

---

### Decide, from a 5-field cron expression and a Date, whether the day-of-month / day-of-week fields admit that date, applying the standard cron OR-of-restricted-fields rule and the 0/7-Sunday normalization.

**Category:** small-utils

**Functions:**
- `cronMatches` in `src/web/src/lib/cron.ts:36` - Full minute/hour/month/dom/dow match. Lines 44-56 build doms/dows, normalize dows.has(7)->add(0), then run the domStar/dowStar EITHER-OR resolution returning domMatch||dowMatch.
- `cronRunsOnDay` in `src/web/src/lib/cron.ts:126` - Day-granularity only (ignores minute/hour). Lines 130-139 are a near copy-paste of cronMatches' month-check + doms/dows build + dows.has(7)->add(0) + identical domStar/dowStar EITHER-OR block, just inlining doms.has(date.getDate())/dows.has(date.getDay()).

**Differences:** cronRunsOnDay is essentially cronMatches with the minute and hour field checks removed. The month-gate, the dom/dow Set construction, the Sunday (7->0) normalization, and the domStar/dowStar/EITHER-OR resolution are duplicated almost verbatim across the two. cronIsDue (line 147) is NOT a duplicate — it correctly composes cronMatches in a minute-stepping loop. parseNaturalSchedule is unrelated (NL->cron).

**Recommendation:** INVESTIGATE - Factor the shared day-match logic (month gate + dom/dow Set build + 7->0 + EITHER-OR resolution) into one helper, e.g. dayFieldsMatch(domF, monF, dowF, date). cronMatches keeps its minute/hour guards then defers to it; cronRunsOnDay becomes a thin wrapper. Reduces the copy-pasted EITHER-OR block that must be kept in sync to one site.

---

### Normalize assistant chat text by stripping the model's own /api/media generated-image markdown, trimming trailing whitespace, collapsing 3+ blank lines to one, and trimming the result.

**Category:** string-utils

**Functions:**
- `appendImageMarkdown` in `src/web/src/lib/chat/image-markdown.ts:24` - Runs the exact 4-step chain (.replace(GENERATED_IMG_MD,'').replace(/[ \t]+$/gm,'').replace(/\n{3,}/g,'\n\n').trim()), THEN appends each image as ![alt](url) with alt sanitized + capped at 120 chars.
- `stripGeneratedImagesForModel` in `src/web/src/lib/chat/image-markdown.ts:50` - Runs the identical 4-step normalization chain and returns. Equivalent to appendImageMarkdown(text, []).

**Differences:** Identical normalization core; appendImageMarkdown additionally appends image markdown afterward. stripGeneratedImagesForModel == appendImageMarkdown with an empty images array.

**Recommendation:** INVESTIGATE - Same file, both intentionally exported as the byte-for-byte-agreeing server+client pair (per the module doc-comment), so this is not a cross-file duplicate. The shared normalization chain could be factored into one private helper (e.g. stripAndNormalize) that both call — appendImageMarkdown would call it then append. Low risk, purely internal; keep both public exports since they have distinct call sites and meanings.

---

### List/single read data hook using a local `fetchJson<T>` helper inside useQuery (same fetch-or-throw-or-parse, but the helper is itself copy-pasted into 3 files)

**Category:** ui-helpers

**Functions:**
- `useConversations` in `src/web/src/hooks/use-conversations.ts:26` - useQuery(['conversations'], fetchJson<{conversations}>('/api/conversations')). fetchJson defined locally at line 20.
- `useProjects` in `src/web/src/hooks/use-projects.ts:33` - useQuery(['projects'], fetchJson<{projects}>('/api/projects')). fetchJson re-defined locally (identical) at line 27.
- `useProject` in `src/web/src/hooks/use-projects.ts:43` - useQuery(['project', id], enabled:!!id, fetchJson<{project}>(`/api/projects/${id}`)). Single-item variant.
- `useConversation` in `src/web/src/hooks/use-conversations.ts:36` - useQuery(['conversation', id], enabled:!!id, fetchJson<{conversation,messages}>(`/api/conversations/${id}`)). Single-item variant.
- `useProjectConversations` in `src/web/src/hooks/use-projects.ts:54` - useQuery(['project-conversations', id], enabled:!!id, fetchJson<{conversations}>(...)). Same pattern.

**Differences:** Same useQuery+fetchJson shape; differ in URL, queryKey, enabled-guard, and unwrapped field. The `fetchJson<T>` helper itself is duplicated verbatim across use-conversations.ts:20, use-projects.ts:27, and use-settings.ts:26 (the latter's useSettings not listed but same helper).

**Recommendation:** CONSOLIDATE - The hooks themselves are legitimately distinct endpoints, but the inline `fetchJson<T>` helper is genuinely copy-pasted 3x and the read-hook bodies are the same boilerplate as group 1. Extracting the helper (and optionally a thin query factory) de-dupes without merging the endpoint-specific hooks.

---

### Create/add mutation hook: useMutation POSTing JSON, parsing { error } on failure, returning the created entity, then invalidating the list query

**Category:** ui-helpers

**Functions:**
- `useAddKnowledge` in `src/web/src/hooks/use-knowledge.ts:23` - POST /api/knowledge; const j=await res.json(); !res.ok throw j.error ?? 'Upload failed'; return j.doc; onSuccess invalidate ['knowledge']
- `useAddSkill` in `src/web/src/hooks/use-skills.ts:24` - POST /api/skills; const j=await res.json(); !res.ok throw j.error ?? 'Save failed'; return j.skill; onSuccess invalidate ['skills']. Identical to useAddKnowledge bar URL/field/message.
- `useCreateProject` in `src/web/src/hooks/use-projects.ts:65` - fetchJson<{project}> POST /api/projects; onSuccess invalidate ['projects']. Same intent via the fetchJson variant (throws res.text() instead of j.error).

**Differences:** useAddKnowledge/useAddSkill are line-for-line identical except URL, returned field (doc/skill) and the default error string. useCreateProject expresses the same create-then-invalidate intent but routes through fetchJson and so reports errors differently.

**Recommendation:** INVESTIGATE - useAddKnowledge and useAddSkill are clear duplicates worth a shared factory. useCreateProject is the same shape but on the fetchJson path, so fold it in only if error-shape is harmonized; otherwise keep the knowledge/skill pair consolidated.

---

### Delete mutation hook: useMutation DELETE by name/id, throw on !res.ok, onSuccess invalidate the list query

**Category:** ui-helpers

**Functions:**
- `useRemoveKnowledge` in `src/web/src/hooks/use-knowledge.ts:40` - DELETE /api/knowledge/${encodeURIComponent(name)}; !res.ok throw 'Delete failed'; invalidate ['knowledge']
- `useRemoveSkill` in `src/web/src/hooks/use-skills.ts:45` - DELETE /api/skills/${encodeURIComponent(name)}; !res.ok throw 'Delete failed'; invalidate ['skills']. Identical to useRemoveKnowledge except the path segment.
- `useDeleteConversation` in `src/web/src/hooks/use-conversations.ts:48` - DELETE /api/conversations/${id}; !res.ok throw r.statusText; invalidate ['conversations']
- `useDeleteProject` in `src/web/src/hooks/use-projects.ts:94` - DELETE /api/projects/${id}; !res.ok throw r.statusText; invalidate ['projects']. Identical to useDeleteConversation except the path segment.

**Differences:** Two near-identical sub-pairs: (Remove* throw 'Delete failed' + encodeURIComponent) and (Delete* throw r.statusText). Across all four only the URL path and the list queryKey to invalidate differ.

**Recommendation:** INVESTIGATE - Four hooks reduce to one parameterized delete factory (id -> DELETE url -> invalidate listKey). They diverge only in trivial error-message text; unify that when folding them.

---

### Format a timestamp as a human 'relative time' string (just now / Nm / Nh / Nd ...) via the same Date.now()-diff cascade

**Category:** ui-helpers

**Functions:**
- `formatLongRelativeTime` in `src/web/src/components/projects/relative-time.ts:5` - In-scope member. Extends the cascade to weeks/months/years with '... ago' suffixes. File comment explicitly says it duplicates+extends the shared formatRelativeTime.
- `formatRelativeTime` in `src/web/src/lib/utils.ts:8` - Shared canonical helper (not in the ui-helpers list). Same diff cascade, stops at days then toLocaleDateString. The 'short' baseline the others fork from.
- `relativeTime` in `src/web/src/components/workbench/tabs/settings-tab.tsx:1221` - Inline file-local copy (not in list). Identical cascade, suffix 'm/h/d ago', falls back to toLocaleDateString.
- `relativeTime` in `src/web/src/components/design/design-view.tsx:1189` - Inline file-local copy (not in list). Same as settings-tab's but suffixes drop the ' ago' ('Nm'/'Nh'/'Nd').

**Differences:** Identical Date.now()-minus-timestamp cascade; differ only in unit suffix wording ('m ago' vs 'm') and how deep the ladder goes (days vs weeks/months/years). The two inline `relativeTime` copies are essentially the same function.

**Recommendation:** CONSOLIDATE - Four implementations of one helper. Add a style/granularity option to the shared formatRelativeTime and delete formatLongRelativeTime + the two inline relativeTime copies. Only formatLongRelativeTime is within the audited set; the rest are listed as context for the consolidation target.

---

### Validate that a GitHub repo identifier matches the allowed character set (owner and repo built from [A-Za-z0-9_.-], rejecting traversal/dot-only segments).

**Category:** validation

**Functions:**
- `validRepoFullName` in `src/web/src/lib/bridge/containers.ts:164` - /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repo) — validates the full 'owner/repo' string in one shot. No explicit dot-only-segment reject (so '../..' technically passes the charset though the '/' split makes pure traversal hard).
- `validName` in `src/web/src/lib/bridge/git-proxy.ts:24` - NAME=/^[A-Za-z0-9_.-]+$/ ; return NAME.test(s) && !/^\.+$/.test(s). Validates ONE segment; parseGitRequest calls it twice (owner, repo). SAME charset as validRepoFullName but adds a dot-only ('.', '..') reject for traversal safety.

**Differences:** Same allowed charset [A-Za-z0-9_.-]. validRepoFullName matches the whole 'owner/repo' at once and omits the dot-only guard; validName matches a single segment and explicitly rejects '.'/'..' segments (the traversal guard). validName is effectively the stricter per-segment half of validRepoFullName.

**Recommendation:** INVESTIGATE - Both encode the identical repo-name policy in different shapes; they can share one validName(segment) primitive, with validRepoFullName implemented as split('/') + two validName calls. git-proxy's version is the safer reference (it rejects dot-only segments). validRepoFullName lives in a different file (containers.ts) and feeds in-container git credential setup, so verify both call sites accept the stricter dot-only reject before unifying — that is a behavior change for validRepoFullName, hence INVESTIGATE not CONSOLIDATE.

---

### Heuristic regex test on user text for explicit 'ask me questions / clarify / need more info' intent before generating a design.

**Category:** validation

**Functions:**
- `userAskedForQuestions` in `src/web/src/lib/design/format.ts:97` - Dedicated detector: /\b(ask\s+me\b|ask\s+(?:some\s+)?questions|questions?\s+first|clarify|need\s+more\s+info|need\s+(?:more\s+)?details|few\s+questions)\b/i.
- `isSparseBrief` in `src/web/src/lib/design/format.ts:124` - Rule #2 inlines a near-identical copy: /\b(ask\s+me\b|ask\s+questions|need\s+more\s+info|clarify|questions?\s+first|few\s+questions)\b/i — and returns true on match. Drifted: missing 'ask some questions' and 'need (more) details' alternations that userAskedForQuestions has.

**Differences:** Same intent (was-I-asked-to-ask-questions). isSparseBrief embeds its own copy of the regex rather than calling userAskedForQuestions, and the two regexes have ALREADY DRIFTED ('ask\s+(?:some\s+)?questions' + 'need\s+(?:more\s+)?details' present only in userAskedForQuestions). isSparseBrief does additional sparseness scoring beyond this branch.

**Recommendation:** CONSOLIDATE - Same file, same intent, already diverging. Replace isSparseBrief's inline rule-#2 regex with a call to userAskedForQuestions(t) so the 'asked for questions' pattern has a single source of truth. The doc comment on isSparseBrief already names userAskedForQuestions as the canonical opt-in path, so this is a low-risk in-file tidy.

---


## LOW Confidence (Possibly Related)

These functions might be related. Review if time permits.

### Deterministically pick one design variant from a candidate list using a numeric seed (Math.abs(seed) % pool.length), after narrowing the pool by a category and falling back to a default pool/value when the category yields nothing.

**Category:** data-transform

**Functions:**
- `pickFontPairing` in `src/web/src/lib/design/format.ts:78`
- `pickTheme` in `src/web/src/lib/design/themes.ts:325`

**Notes:** Different domains and return types (FontPairing keyed by .bias array vs Theme keyed by aesthetic lookup map), different pool-narrowing mechanism (filter vs object index), and pickFontPairing has a random fallback path that pickTheme lacks. Only the `Math.abs(seed) % len` selection core is shared.

---

### List models installed in the local Ollama daemon by fetching /api/tags

**Category:** http-api

**Functions:**
- `GET` in `src/web/src/app/api/ollama/models/route.ts:18`
- `GET` in `src/web/src/app/api/providers/ollama-models/route.ts:9`

**Notes:** Both ultimately GET the daemon's /api/tags and surface installed models, but differ in consumer + contract: ollama/models is a settings/management diagnostic ({ok,baseURL,version,...}, 502 on failure, full metadata); providers/ollama-models is a fire-and-forget picker feed ({models}, always 200, deduped name list). Different error semantics and response shapes.

---

### Compare a stored timestamp against now plus a window to decide liveness/expiry (Date.now()-based time-window predicate).

**Category:** validation

**Functions:**
- `isEnvironmentOnline` in `src/web/src/lib/bridge/store.ts:491`
- `isExpired` in `src/web/src/lib/mcp/oauth-store.ts:117`

**Notes:** Structurally both are 'timestamp + window vs now'. But they operate on different domains and opposite polarity: isEnvironmentOnline uses a fixed constant TTL on a heartbeat (true = healthy), isExpired uses a per-token server-provided expires_in with a 60s safety margin (true = stale). Different inputs, different meaning.

---

