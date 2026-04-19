import { loadConfig } from "./config/load.ts";
import { startBridge } from "./bridge/server.ts";

const cfg = loadConfig();
startBridge({ host: cfg.host, port: cfg.port });
console.log(`[misty-core] listening on http://${cfg.host}:${cfg.port} (provider=${cfg.provider} model=${cfg.model})`);
