import { tool } from "ai";
import { z } from "zod";
import { addMemory, listMemories, removeMemory } from "@/lib/chat/memory";

const inputSchema = z.object({
  action: z
    .enum(["add", "list", "remove"])
    .describe(
      'What to do: "add" saves a new fact, "list" shows saved memories with their ids, "remove" deletes one by id.',
    ),
  content: z
    .string()
    .optional()
    .describe(
      'For "add": the fact to remember — one short, self-contained sentence (e.g. "Prefers TypeScript over Python").',
    ),
  id: z
    .string()
    .optional()
    .describe('For "remove": the id of the memory to delete (get ids via "list").'),
});

/**
 * Build the `memory` tool for one chat request, closing over the
 * authenticated user's id (same per-request factory pattern as
 * createGenerateImageTool) — the model can never address another user's
 * memories. Saved memories are injected into every future chat's system
 * prompt via buildMemoryBlock (src/lib/chat/memory.ts), so recall is
 * automatic; "list" exists mainly to fetch ids for "remove".
 *
 * execute never throws: data-layer failures (empty content, oversized fact,
 * missing DATABASE_URL, bad id) come back as { status: "error" } results the
 * model can read and relay, mirroring web-search/generate-image.
 */
export function createMemoryTool(userId: string) {
  return tool({
    description:
      "Persistent memory about the user, shared across all their conversations. " +
      'Use action "add" whenever the user shares a durable fact worth remembering — their name, preferences, projects, goals, important context — or explicitly asks you to remember something. Keep each memory one short, self-contained sentence. ' +
      'Use "remove" (with an id from "list") when the user asks you to forget something or a saved fact becomes outdated. ' +
      "Saved memories are shown to you automatically in future conversations, so you do NOT need to call this tool to recall them. " +
      "Never save secrets (passwords, API keys) or throwaway details.",
    inputSchema,
    execute: async ({ action, content, id }) => {
      try {
        switch (action) {
          case "add": {
            if (!content?.trim()) {
              return {
                status: "error" as const,
                error: '`content` is required for action "add".',
              };
            }
            const row = await addMemory(userId, content);
            return {
              status: "ok" as const,
              action,
              memory: { id: row.id, content: row.content },
            };
          }
          case "list": {
            const rows = await listMemories(userId);
            return {
              status: "ok" as const,
              action,
              count: rows.length,
              memories: rows.map((r) => ({ id: r.id, content: r.content })),
            };
          }
          case "remove": {
            if (!id?.trim()) {
              return {
                status: "error" as const,
                error: '`id` is required for action "remove".',
              };
            }
            const removed = await removeMemory(userId, id.trim());
            if (!removed) {
              return {
                status: "error" as const,
                error: `No memory found with id ${id.trim()}.`,
              };
            }
            return { status: "ok" as const, action, removedId: id.trim() };
          }
          default:
            // Unreachable through the zod schema; guards direct calls.
            return {
              status: "error" as const,
              error: `Unknown action: ${String(action)}. Use "add", "list", or "remove".`,
            };
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        console.error("[memory-tool] failed:", message);
        return {
          status: "error" as const,
          error: `Memory operation failed: ${message}`,
        };
      }
    },
  });
}
