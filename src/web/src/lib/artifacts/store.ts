// DB layer for claude.ai-style artifacts (System B). Wires the previously
// unused `web.artifacts` + `web.artifact_versions` tables. Ownership flows
// through the conversation (artifacts.conversationId → conversations.userId),
// mirroring how messages are owned. Share tokens mirror the workspace
// share-token pattern (32-char hex, 7-day TTL) but live in Postgres so the
// public page is DB-resolvable.

import { and, desc, eq, inArray } from "drizzle-orm";
import { randomUUID } from "node:crypto";
import { db, schema } from "@/lib/db";
import type { JarvisArtifact } from "@/lib/actions/types";
import type { Artifact, ArtifactVersion } from "@/lib/db/schema";
import { extractJarvisArtifacts } from "./extract";
import { detectArtifacts } from "./detect";

const SHARE_TTL_MS = 7 * 24 * 60 * 60 * 1000;

export type ArtifactWithVersions = Artifact & { versions: ArtifactVersion[] };

// An artifact is owned by whoever owns its conversation. Returns the
// artifact id if owned by `userId`, else null.
async function ownedArtifactId(
  id: string,
  userId: string,
): Promise<string | null> {
  if (!db) return null;
  const [row] = await db
    .select({ id: schema.artifacts.id })
    .from(schema.artifacts)
    .innerJoin(
      schema.conversations,
      eq(schema.artifacts.conversationId, schema.conversations.id),
    )
    .where(
      and(eq(schema.artifacts.id, id), eq(schema.conversations.userId, userId)),
    )
    .limit(1);
  return row?.id ?? null;
}

// Persist + version every artifact the model emitted this turn.
// ponytail: no explicit transaction — onFinish is effectively serialized
// per conversation (one stream at a time), so the find-then-insert race
// can't fire in practice. Wrap in db.transaction() if concurrent writes to
// one conversation ever become real.
export async function upsertArtifactFromMessage({
  conversationId,
  messageId,
  artifacts,
}: {
  conversationId: string;
  messageId: string | null;
  artifacts: JarvisArtifact[];
}): Promise<void> {
  if (!db || artifacts.length === 0) return;
  for (const a of artifacts) {
    const [existing] = await db
      .select({ id: schema.artifacts.id })
      .from(schema.artifacts)
      .where(
        and(
          eq(schema.artifacts.conversationId, conversationId),
          eq(schema.artifacts.slug, a.slug),
        ),
      )
      .limit(1);

    let artifactId: string;
    if (!existing) {
      const [created] = await db
        .insert(schema.artifacts)
        .values({
          conversationId,
          slug: a.slug,
          title: a.title,
          kind: a.kind,
        })
        .returning({ id: schema.artifacts.id });
      artifactId = created.id;
    } else {
      artifactId = existing.id;
      await db
        .update(schema.artifacts)
        .set({ title: a.title, kind: a.kind, updatedAt: new Date() })
        .where(eq(schema.artifacts.id, artifactId));
    }

    // All existing versions — for next-number + dedup. Dedup against ANY
    // version (not just the latest) so re-running backfill is idempotent and
    // a revert to an earlier content doesn't create a redundant version.
    const existingVersions = await db
      .select({
        version: schema.artifactVersions.version,
        content: schema.artifactVersions.content,
      })
      .from(schema.artifactVersions)
      .where(eq(schema.artifactVersions.artifactId, artifactId));

    if (existingVersions.some((v) => v.content === a.content)) continue;
    const nextVersion =
      existingVersions.reduce((mx, v) => Math.max(mx, v.version), 0) + 1;

    await db.insert(schema.artifactVersions).values({
      artifactId,
      version: nextVersion,
      content: a.content,
      language: a.language ?? null,
      messageId: messageId ?? null,
    });
  }
}

export type ArtifactListItem = Artifact & {
  // Latest version's content + language — lets the gallery render a live
  // thumbnail without an extra round-trip per card.
  latestContent: string;
  latestLanguage: string | null;
};

// All artifacts owned by the user, newest first, each with its latest
// version's content (for thumbnails). Two queries (artifacts + their
// versions) — no N+1.
export async function listArtifacts(
  userId: string,
): Promise<ArtifactListItem[]> {
  if (!db) return [];
  const arts = await db
    .select()
    .from(schema.artifacts)
    .innerJoin(
      schema.conversations,
      eq(schema.artifacts.conversationId, schema.conversations.id),
    )
    .where(eq(schema.conversations.userId, userId))
    .orderBy(desc(schema.artifacts.updatedAt))
    .limit(500);
  const rows = arts.map((r) => r.artifacts);
  if (rows.length === 0) return [];

  const versions = await db
    .select({
      artifactId: schema.artifactVersions.artifactId,
      version: schema.artifactVersions.version,
      content: schema.artifactVersions.content,
      language: schema.artifactVersions.language,
    })
    .from(schema.artifactVersions)
    .where(
      inArray(
        schema.artifactVersions.artifactId,
        rows.map((r) => r.id),
      ),
    );
  const latest = new Map<
    string,
    { version: number; content: string; language: string | null }
  >();
  for (const v of versions) {
    const cur = latest.get(v.artifactId);
    if (!cur || v.version > cur.version)
      latest.set(v.artifactId, {
        version: v.version,
        content: v.content,
        language: v.language,
      });
  }
  return rows.map((r) => ({
    ...r,
    latestContent: latest.get(r.id)?.content ?? "",
    latestLanguage: latest.get(r.id)?.language ?? null,
  }));
}

export async function getArtifact(
  id: string,
  userId: string,
): Promise<ArtifactWithVersions | null> {
  if (!db) return null;
  if (!(await ownedArtifactId(id, userId))) return null;
  const [art] = await db
    .select()
    .from(schema.artifacts)
    .where(eq(schema.artifacts.id, id))
    .limit(1);
  if (!art) return null;
  const versions = await db
    .select()
    .from(schema.artifactVersions)
    .where(eq(schema.artifactVersions.artifactId, id))
    .orderBy(schema.artifactVersions.version);
  return { ...art, versions };
}

// Artifacts for one conversation (with versions) — hydrates the in-chat
// panel on reload. Ownership checked via the conversation.
export async function getConversationArtifacts(
  conversationId: string,
  userId: string,
): Promise<ArtifactWithVersions[]> {
  if (!db) return [];
  const [conv] = await db
    .select({ id: schema.conversations.id })
    .from(schema.conversations)
    .where(
      and(
        eq(schema.conversations.id, conversationId),
        eq(schema.conversations.userId, userId),
      ),
    )
    .limit(1);
  if (!conv) return [];
  const arts = await db
    .select()
    .from(schema.artifacts)
    .where(eq(schema.artifacts.conversationId, conversationId))
    .orderBy(schema.artifacts.createdAt);
  const out: ArtifactWithVersions[] = [];
  for (const art of arts) {
    const versions = await db
      .select()
      .from(schema.artifactVersions)
      .where(eq(schema.artifactVersions.artifactId, art.id))
      .orderBy(schema.artifactVersions.version);
    out.push({ ...art, versions });
  }
  return out;
}

export async function renameArtifact(
  id: string,
  userId: string,
  title: string,
): Promise<boolean> {
  if (!db) return false;
  if (!(await ownedArtifactId(id, userId))) return false;
  await db
    .update(schema.artifacts)
    .set({ title: title.slice(0, 200), updatedAt: new Date() })
    .where(eq(schema.artifacts.id, id));
  return true;
}

export async function deleteArtifact(
  id: string,
  userId: string,
): Promise<boolean> {
  if (!db) return false;
  if (!(await ownedArtifactId(id, userId))) return false;
  // artifact_versions cascade-delete via the FK.
  await db.delete(schema.artifacts).where(eq(schema.artifacts.id, id));
  return true;
}

// Mint (or rotate) a public share token. Returns null if not owned.
export async function setArtifactShareToken(
  id: string,
  userId: string,
): Promise<{ token: string; expiresAt: Date } | null> {
  if (!db) return null;
  if (!(await ownedArtifactId(id, userId))) return null;
  const token = randomUUID().replace(/-/g, "");
  const expiresAt = new Date(Date.now() + SHARE_TTL_MS);
  await db
    .update(schema.artifacts)
    .set({ shareToken: token, shareExpiresAt: expiresAt })
    .where(eq(schema.artifacts.id, id));
  return { token, expiresAt };
}

export async function clearArtifactShareToken(
  id: string,
  userId: string,
): Promise<boolean> {
  if (!db) return false;
  if (!(await ownedArtifactId(id, userId))) return false;
  await db
    .update(schema.artifacts)
    .set({ shareToken: null, shareExpiresAt: null })
    .where(eq(schema.artifacts.id, id));
  return true;
}

// Public lookup — no userId. Returns the latest version's renderable
// content, or null if the token is unknown/expired.
export async function getArtifactByShareToken(token: string): Promise<{
  kind: Artifact["kind"];
  title: string;
  content: string;
  language: string | null;
} | null> {
  if (!db || !token) return null;
  const [art] = await db
    .select()
    .from(schema.artifacts)
    .where(eq(schema.artifacts.shareToken, token))
    .limit(1);
  if (!art) return null;
  if (art.shareExpiresAt && art.shareExpiresAt.getTime() < Date.now())
    return null;
  const [latest] = await db
    .select({
      content: schema.artifactVersions.content,
      language: schema.artifactVersions.language,
    })
    .from(schema.artifactVersions)
    .where(eq(schema.artifactVersions.artifactId, art.id))
    .orderBy(desc(schema.artifactVersions.version))
    .limit(1);
  if (!latest) return null;
  return {
    kind: art.kind,
    title: art.title,
    content: latest.content,
    language: latest.language,
  };
}

function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((p) =>
        p && typeof p === "object" && (p as { type?: string }).type === "text"
          ? String((p as { text?: string }).text ?? "")
          : "",
      )
      .join("");
  }
  return "";
}

// One-time (idempotent) scan of the user's whole chat history: extract both
// explicit <jarvisArtifact> tags and naturally-emitted substantial code/HTML/
// SVG/mermaid blocks, and persist them so the gallery reflects past work —
// the way claude.ai's library aggregates artifacts across all conversations.
// Safe to re-run: upsert dedups identical content per (conversation, slug).
export async function backfillArtifactsForUser(
  userId: string,
): Promise<{ scanned: number; artifacts: number }> {
  if (!db) return { scanned: 0, artifacts: 0 };
  const msgs = await db
    .select({
      id: schema.messages.id,
      conversationId: schema.messages.conversationId,
      content: schema.messages.content,
    })
    .from(schema.messages)
    .innerJoin(
      schema.conversations,
      eq(schema.messages.conversationId, schema.conversations.id),
    )
    .where(
      and(
        eq(schema.conversations.userId, userId),
        eq(schema.messages.role, "assistant"),
      ),
    )
    .orderBy(schema.messages.createdAt);

  for (const msg of msgs) {
    const text = messageText(msg.content);
    if (!text) continue;
    const artifacts = [
      ...extractJarvisArtifacts(text),
      ...detectArtifacts(text),
    ];
    if (artifacts.length === 0) continue;
    await upsertArtifactFromMessage({
      conversationId: msg.conversationId,
      messageId: msg.id,
      artifacts,
    });
  }

  const total = await listArtifacts(userId);
  return { scanned: msgs.length, artifacts: total.length };
}
