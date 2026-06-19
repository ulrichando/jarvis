import { NextResponse } from "next/server";

// Reports which providers have an API key configured. The picker uses
// this to dim models from providers that aren't usable yet (so they
// stay visible — adding the key in .env.local + restarting the dev
// server flips them on without any UI change).
//
// Server-side only: keys live in env, browser never sees them. Clients
// just see booleans.
export async function GET() {
  return NextResponse.json({
    anthropic: !!process.env.ANTHROPIC_API_KEY,
    openai:    !!process.env.OPENAI_API_KEY,
    google:    !!(
      process.env.GOOGLE_GENERATIVE_AI_API_KEY ||
      process.env.GOOGLE_API_KEY
    ),
    deepseek:  !!process.env.DEEPSEEK_API_KEY,
    groq:      !!process.env.GROQ_API_KEY,
    kimi:      !!process.env.KIMI_API_KEY,
    // Local Ollama needs no key — always "available" (the daemon may or may
    // not be running, but there's no key to gate on).
    ollama:    true,
  });
}
