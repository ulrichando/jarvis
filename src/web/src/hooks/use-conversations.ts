"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UIMessage } from "ai";

export type ConversationSummary = {
  id: string;
  title: string;
  model: string;
  updatedAt: string;
  // Optional — only the /chats listing reads these (project tag +
  // "Filter by project"). Other consumers (sidebar, search) ignore them.
  projectId?: string | null;
  projectName?: string | null;
};

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<T>;
}

export function useConversations() {
  return useQuery({
    queryKey: ["conversations"],
    queryFn: () =>
      fetchJson<{ conversations: ConversationSummary[] }>("/api/conversations").then(
        (d) => d.conversations,
      ),
  });
}

export function useConversation(id: string | undefined) {
  return useQuery({
    queryKey: ["conversation", id],
    enabled: !!id,
    queryFn: () =>
      fetchJson<{
        conversation: ConversationSummary;
        messages: UIMessage[];
      }>(`/api/conversations/${id}`),
  });
}

export function useDeleteConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetch(`/api/conversations/${id}`, { method: "DELETE" }).then((r) => {
        if (!r.ok) throw new Error(r.statusText);
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
}

export function useRenameConversation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, title }: { id: string; title: string }) => {
      const r = await fetch(`/api/conversations/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (!r.ok) throw new Error(await r.text());
    },
    // Optimistic — flip the cached title immediately so the rename
    // feels instant. The server PATCH only stores the new value,
    // doesn't return one, so there's nothing to reconcile on success.
    onMutate: async ({ id, title }) => {
      await qc.cancelQueries({ queryKey: ["conversations"] });
      const prev = qc.getQueryData<ConversationSummary[]>(["conversations"]);
      qc.setQueryData<ConversationSummary[]>(["conversations"], (old) =>
        old?.map((c) => (c.id === id ? { ...c, title } : c)),
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["conversations"], ctx.prev);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
  });
}
