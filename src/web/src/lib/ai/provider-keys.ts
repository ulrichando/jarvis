import "server-only";

import type { Provider } from "./models-meta";

/**
 * Single source of truth for the environment variable each provider's API key
 * falls back to when no key is stored in the web app's own settings.json.
 *
 * These are the same vars the voice-agent / CLI use, and that next.config.ts
 * loads from ~/.jarvis/keys.env — so a key entered on the desktop tray is
 * visible AND usable here. Routed through one function so models.ts (resolving
 * a key for a real call) and settings/store.ts (reporting key presence to the
 * Providers UI) can never drift apart.
 */
export function providerEnvKey(provider: Provider): string | undefined {
  switch (provider) {
    case "anthropic":
      return process.env.ANTHROPIC_API_KEY;
    case "openai":
      return process.env.OPENAI_API_KEY;
    case "google":
      return (
        process.env.GOOGLE_GENERATIVE_AI_API_KEY ?? process.env.GOOGLE_API_KEY
      );
    case "deepseek":
      return process.env.DEEPSEEK_API_KEY;
    case "groq":
      return process.env.GROQ_API_KEY;
    case "kimi":
      return process.env.KIMI_API_KEY;
  }
}
