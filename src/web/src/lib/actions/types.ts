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
