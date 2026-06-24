// Action wire format. Mirrors bolt.diy's XML schema (`<boltArtifact>` /
// `<boltAction>`) so any LLM that has been trained on bolt.new examples
// "just works" without needing to learn a JARVIS-specific format.

export type ActionType = "file" | "shell" | "start";

export type FileAction = {
  type: "file";
  filePath: string;
  content: string;
};

export type ShellAction = {
  type: "shell";
  content: string;
};

export type StartAction = {
  type: "start";
  content: string;
};

export type Action = FileAction | ShellAction | StartAction;

export type ArtifactData = {
  id: string;
  title: string;
  type?: string;
};

// claude.ai-style self-contained artifact (System B). Distinct from the
// bolt ArtifactData above (System A, multi-file app actions). Carried in
// the chat text stream via <jarvisArtifact> and persisted/versioned in
// the `web.artifacts` + `web.artifact_versions` tables.
export type ArtifactKind =
  | "code"
  | "markdown"
  | "html"
  | "react"
  | "svg"
  | "mermaid"
  | "csv"
  | "json";

export type JarvisArtifact = {
  // Stable per-conversation identity. The model reuses the same slug when
  // revising an artifact (→ a new version); a new slug → a new artifact.
  slug: string;
  title: string;
  kind: ArtifactKind;
  language?: string;
  content: string;
};

export type ActionStatus =
  | "queued"
  | "running"
  | "success"
  | "error"
  | "skipped";

export type TrackedAction = {
  artifactId: string;
  actionId: string;
  action: Action;
  status: ActionStatus;
  error?: string;
};
