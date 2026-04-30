import { listWorkspaces, createWorkspace } from "@/lib/workspace/storage";
import { DesignView } from "@/components/design/design-view";

type SearchParams = Promise<{ ws?: string | string[] }>;

export default async function DesignPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const { ws } = await searchParams;
  const wsId = Array.isArray(ws) ? ws[0] : ws;

  const all = await listWorkspaces();

  // Resolution order:
  //   1. ?ws=<id> in the URL → use that workspace if it exists
  //   2. Else: most-recently-touched workspace if any exist
  //   3. Else: create the first one ("My first design")
  let workspace = wsId ? all.find((w) => w.id === wsId) : null;
  if (!workspace) {
    workspace = all[0] ?? (await createWorkspace("My first design"));
  }

  // Sort by recency so the picker dropdown shows latest first.
  const projects = [...all].sort((a, b) => b.updatedAt - a.updatedAt);

  return (
    <DesignView
      workspaceId={workspace.id}
      workspaceName={workspace.name}
      projects={projects}
    />
  );
}
