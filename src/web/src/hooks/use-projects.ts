"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type ProjectSummary = {
  id: string;
  name: string;
  description: string;
  badge: string | null;
  isFavorite: boolean;
  createdAt: string;
  updatedAt: string;
};

export type Project = ProjectSummary & {
  instructions: string;
  userId: string;
};

export type ProjectConversation = {
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

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: () =>
      fetchJson<{ projects: ProjectSummary[] }>("/api/projects").then(
        (d) => d.projects,
      ),
  });
}

export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: ["project", id],
    enabled: !!id,
    queryFn: () =>
      fetchJson<{ project: Project }>(`/api/projects/${id}`).then(
        (d) => d.project,
      ),
  });
}

export function useProjectConversations(id: string | undefined) {
  return useQuery({
    queryKey: ["project-conversations", id],
    enabled: !!id,
    queryFn: () =>
      fetchJson<{ conversations: ProjectConversation[] }>(
        `/api/projects/${id}/conversations`,
      ).then((d) => d.conversations),
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; description?: string }) =>
      fetchJson<{ project: Project }>("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }).then((d) => d.project),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useUpdateProject(id: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<Pick<Project, "name" | "description" | "instructions" | "isFavorite">>) =>
      fetchJson<{ project: Project }>(`/api/projects/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      }).then((d) => d.project),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["project", id] });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetch(`/api/projects/${id}`, { method: "DELETE" }).then((r) => {
        if (!r.ok) throw new Error(r.statusText);
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}
