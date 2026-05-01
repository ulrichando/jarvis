"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ChevronDown, FolderKanban, Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useProjects, type ProjectSummary } from "@/hooks/use-projects";
import { CreateProjectDialog } from "./create-project-dialog";
import { formatLongRelativeTime } from "./relative-time";

type SortKey = "activity" | "name" | "created";

const SORT_LABEL: Record<SortKey, string> = {
  activity: "Activity",
  name: "Name",
  created: "Date created",
};

export function ProjectsView() {
  const { data: projects = [], isLoading } = useProjects();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("activity");
  const [createOpen, setCreateOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = q
      ? projects.filter(
          (p) =>
            p.name.toLowerCase().includes(q) ||
            p.description.toLowerCase().includes(q),
        )
      : [...projects];

    list.sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "created")
        return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
      return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime();
    });
    return list;
  }, [projects, query, sort]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl px-6 pt-10 pb-12">
          {/* Title row */}
          <div className="flex items-center justify-between">
            <h1 className="font-serif text-[34px] font-semibold tracking-tight leading-none">
              Projects
            </h1>
            <Button
              variant="outline"
              size="sm"
              className="rounded-md"
              onClick={() => setCreateOpen(true)}
            >
              <Plus className="size-3.5" />
              New project
            </Button>
          </div>

          {/* Search */}
          <div className="relative mt-6">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/70" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search projects..."
              className="h-10 pl-9 rounded-lg"
            />
          </div>

          {/* Sort */}
          <div className="mt-3 flex items-center justify-end gap-2 text-[13px]">
            <span className="text-muted-foreground">Sort by</span>
            <div className="relative">
              <Button
                variant="outline"
                size="sm"
                className="rounded-md"
                onClick={() => setSortOpen((v) => !v)}
                onBlur={() => setTimeout(() => setSortOpen(false), 120)}
              >
                {SORT_LABEL[sort]}
                <ChevronDown className="size-3.5" />
              </Button>
              {sortOpen && (
                <div className="absolute right-0 top-full z-20 mt-1 min-w-36 rounded-md border border-border/60 bg-popover p-1 text-popover-foreground shadow-md">
                  {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
                    <button
                      key={k}
                      type="button"
                      onMouseDown={(e) => {
                        e.preventDefault();
                        setSort(k);
                        setSortOpen(false);
                      }}
                      className={cn(
                        "flex w-full items-center rounded-sm px-2 py-1.5 text-left text-[13px] transition-colors hover:bg-muted",
                        sort === k && "text-primary",
                      )}
                    >
                      {SORT_LABEL[k]}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Cards */}
          <div className="mt-6">
            {isLoading && projects.length === 0 ? (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {[0, 1, 2, 3].map((i) => (
                  <div
                    key={i}
                    className="h-36 rounded-xl border border-border/60 bg-card/40 animate-pulse"
                  />
                ))}
              </div>
            ) : filtered.length === 0 ? (
              <EmptyState
                hasQuery={query.trim().length > 0}
                onCreate={() => setCreateOpen(true)}
              />
            ) : (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {filtered.map((p) => (
                  <ProjectCard key={p.id} project={p} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <CreateProjectDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}

function ProjectCard({ project }: { project: ProjectSummary }) {
  return (
    <Link
      href={`/projects/${project.id}`}
      className="group flex h-36 flex-col rounded-xl border border-border/60 bg-card/40 px-5 py-4 transition-colors hover:border-border hover:bg-card"
    >
      <div className="flex items-start gap-2">
        <h3 className="text-[15px] font-semibold tracking-tight text-foreground">
          {project.name}
        </h3>
        {project.badge && (
          <span className="mt-0.5 rounded-md bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {project.badge}
          </span>
        )}
      </div>
      <p className="mt-1.5 line-clamp-3 text-[13px] leading-5 text-muted-foreground">
        {project.description}
      </p>
      <div className="mt-auto pt-2 text-[12px] text-muted-foreground/70">
        Updated {formatLongRelativeTime(project.updatedAt)}
      </div>
    </Link>
  );
}

function EmptyState({
  hasQuery,
  onCreate,
}: {
  hasQuery: boolean;
  onCreate: () => void;
}) {
  if (hasQuery) {
    return (
      <div className="rounded-xl border border-dashed border-border/60 px-6 py-14 text-center">
        <p className="text-sm text-muted-foreground">
          No projects match your search.
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-border/60 px-6 py-16 text-center">
      <FolderKanban className="size-7 text-muted-foreground/60" />
      <p className="mt-3 text-sm text-muted-foreground">
        No projects yet. Bundle chats, files, and instructions into a workspace.
      </p>
      <Button
        size="sm"
        className="mt-4 rounded-md"
        variant="outline"
        onClick={onCreate}
      >
        <Plus className="size-3.5" />
        New project
      </Button>
    </div>
  );
}
