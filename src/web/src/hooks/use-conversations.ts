"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UIMessage } from "ai";

export type ConversationSummary = {
  id: string;
  title: string;
  model: string;
  updatedAt: string;
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
