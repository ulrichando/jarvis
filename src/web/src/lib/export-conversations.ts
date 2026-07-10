// Shared conversation export used by Settings → Data and Settings → Privacy,
// which previously each had an identical copy that swallowed failures: a
// failed per-conversation fetch became `null` in the dump, yet the UI still
// toasted "Exported N chats". This counts failures honestly so the caller can
// tell the user the truth.

export type ExportResult = {
  /** Successfully-fetched conversation bodies, ready to serialize. */
  data: unknown[];
  /** How many were requested. */
  requested: number;
  /** How many could not be fetched (non-2xx or network error). */
  failed: number;
};

/**
 * Fetch every conversation by id, counting failures instead of hiding them.
 * `fetchFn` is injectable for tests. Never throws — a fully-failed export
 * returns `{ data: [], failed: requested }` so the caller decides how to report.
 */
export async function fetchConversationsForExport(
  ids: string[],
  fetchFn: typeof fetch = fetch,
): Promise<ExportResult> {
  const results = await Promise.all(
    ids.map(async (id) => {
      try {
        const r = await fetchFn(`/api/conversations/${id}`);
        if (!r.ok) return { ok: false as const };
        return { ok: true as const, body: (await r.json()) as unknown };
      } catch {
        return { ok: false as const };
      }
    }),
  );
  const data = results.flatMap((r) => (r.ok ? [r.body] : []));
  return { data, requested: ids.length, failed: results.length - data.length };
}

/** Browser-only: download `data` as a pretty-printed JSON file. */
export function triggerJsonDownload(data: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
