import type { TreeEntry } from "@/lib/workspace/client";

export type FileGroupKey =
  | "folders"
  | "pages"
  | "components"
  | "stylesheets"
  | "scripts"
  | "references"
  | "other";

export const GROUP_LABEL: Record<FileGroupKey, string> = {
  folders: "Folders",
  pages: "Pages",
  components: "Components",
  stylesheets: "Stylesheets",
  scripts: "Scripts",
  references: "References",
  other: "Other",
};

const PAGE_EXT = new Set(["html", "htm", "md", "mdx"]);
const COMPONENT_EXT = new Set(["jsx", "tsx", "vue", "svelte"]);
const STYLE_EXT = new Set(["css", "scss", "sass", "less"]);
const SCRIPT_EXT = new Set([
  "js", "ts", "mjs", "cjs", "json",
  "py", "rb", "go", "rs", "sh",
]);
const REFERENCE_EXT = new Set([
  "png", "jpg", "jpeg", "gif", "webp", "svg", "avif", "pdf", "txt",
]);

export function ext(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

export function classify(entry: TreeEntry): FileGroupKey {
  if (entry.type === "dir") return "folders";
  const e = ext(entry.name);
  if (PAGE_EXT.has(e)) return "pages";
  if (COMPONENT_EXT.has(e)) return "components";
  if (STYLE_EXT.has(e)) return "stylesheets";
  if (SCRIPT_EXT.has(e)) return "scripts";
  if (REFERENCE_EXT.has(e)) return "references";
  return "other";
}

export function groupEntries(entries: TreeEntry[]): Record<FileGroupKey, TreeEntry[]> {
  const groups: Record<FileGroupKey, TreeEntry[]> = {
    folders: [],
    pages: [],
    components: [],
    stylesheets: [],
    scripts: [],
    references: [],
    other: [],
  };
  for (const e of entries) groups[classify(e)].push(e);
  return groups;
}

export function fileKindLabel(entry: TreeEntry): string {
  if (entry.type === "dir") return "Folder";
  const e = ext(entry.name);
  if (PAGE_EXT.has(e)) return e === "md" || e === "mdx" ? "Markdown" : "HTML page";
  if (COMPONENT_EXT.has(e)) return e.toUpperCase() + " component";
  if (STYLE_EXT.has(e)) return "Stylesheet";
  if (SCRIPT_EXT.has(e)) return e.toUpperCase();
  if (REFERENCE_EXT.has(e)) return "Reference";
  return e ? e.toUpperCase() : "File";
}

export const IMAGE_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "avif"]);
