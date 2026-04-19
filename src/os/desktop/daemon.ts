import { loadConfig } from "./config/load.ts";
import { startBridge } from "./bridge/server.ts";
import { createClient } from "./providers/registry.ts";
import { createVisionClient } from "./providers/vision.ts";
import { defaultTools } from "./agent/tools/index.ts";

const cfg = loadConfig();
const client = createClient(cfg);

// Vision client is optional — only required when the screen tool is actually invoked.
let visionClient;
try {
  visionClient = createVisionClient(cfg);
} catch {
  visionClient = undefined;
}

const tools = defaultTools({ visionClient });

startBridge({
  host: cfg.host,
  port: cfg.port,
  client,
  defaultModel: cfg.model,
  tools,
});
console.log(`[misty-core] listening on http://${cfg.host}:${cfg.port} (provider=${cfg.provider} model=${cfg.model}, vision=${visionClient?.name ?? "disabled"})`);
