"use client";

// Tiny fetch wrappers for the workbench. Server-side path validation
// lives in lib/workspace/storage.ts; here we just hit the API.

export type Workspace = {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
};

export type TreeEntry = {
  name: string;
  path: string;
  type: "file" | "dir";
};

export async function apiListWorkspaces(): Promise<Workspace[]> {
  const r = await fetch("/api/workspace");
  const j = await r.json();
  return j.workspaces ?? [];
}

export async function apiCreateWorkspace(name: string): Promise<Workspace> {
  const r = await fetch("/api/workspace", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  const j = await r.json();
  return j.workspace;
}

export async function apiDeleteWorkspace(id: string): Promise<void> {
  await fetch(`/api/workspace/${id}`, { method: "DELETE" });
}

export async function apiRenameWorkspace(
  id: string,
  name: string,
): Promise<Workspace> {
  const r = await fetch(`/api/workspace/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j?.error ?? `rename failed (${r.status})`);
  }
  const j = await r.json();
  return j.workspace;
}

export async function apiTree(id: string, path = ""): Promise<TreeEntry[]> {
  const r = await fetch(
    `/api/workspace/${id}/tree?path=${encodeURIComponent(path)}`,
  );
  const j = await r.json();
  return j.entries ?? [];
}

export async function apiReadFile(id: string, path: string): Promise<string> {
  const r = await fetch(
    `/api/workspace/${id}/file?path=${encodeURIComponent(path)}`,
  );
  const j = await r.json();
  if (!r.ok) throw new Error(j.error ?? "read failed");
  return j.content ?? "";
}

export async function apiWriteFile(
  id: string,
  path: string,
  content: string,
): Promise<void> {
  const r = await fetch(`/api/workspace/${id}/file`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? "write failed");
  }
}

export async function apiCreateEntry(
  id: string,
  path: string,
  type: "file" | "dir",
): Promise<void> {
  const r = await fetch(`/api/workspace/${id}/file`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, type }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({}));
    throw new Error(j.error ?? "create failed");
  }
}

export async function apiDeleteEntry(id: string, path: string): Promise<void> {
  await fetch(
    `/api/workspace/${id}/file?path=${encodeURIComponent(path)}`,
    { method: "DELETE" },
  );
}
