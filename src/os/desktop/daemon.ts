import { loadConfig } from "./config/load.ts";
import { startBridge } from "./bridge/server.ts";
import { createClient } from "./providers/registry.ts";
import { defaultTools } from "./agent/tools/index.ts";

const cfg = loadConfig();
const client = createClient(cfg);
const tools = defaultTools();

startBridge({
  host: cfg.host,
  port: cfg.port,
  client,
  defaultModel: cfg.model,
  tools,
});
console.log(`[misty-core] listening on http://${cfg.host}:${cfg.port} (provider=${cfg.provider} model=${cfg.model})`);
