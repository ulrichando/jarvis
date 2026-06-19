import { NextResponse } from "next/server";

import { fetchInstalledOllamaModels } from "@/lib/ai/ollama-discovery";

// Lists models installed in the local Ollama daemon. The model picker merges
// these into the "Local (Ollama)" group so every pulled model is selectable,
// not just the two hardcoded in models-meta.ts. Returns an empty list (200)
// when the daemon is offline — the picker then shows only the static entries.
export async function GET() {
  const models = await fetchInstalledOllamaModels();
  return NextResponse.json({ models });
}
