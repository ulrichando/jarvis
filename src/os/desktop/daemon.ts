import { loadConfig } from "./config/load.ts";
import { startBridge } from "./bridge/server.ts";
import { createClient } from "./providers/registry.ts";
import { createVisionClient } from "./providers/vision.ts";
import { defaultTools } from "./agent/tools/index.ts";
import { ConfirmationQueue } from "./voice/confirmations.ts";
import { EventBus } from "./bridge/events.ts";
import { PanelState } from "./panels/state.ts";

const cfg = loadConfig();
const client = createClient(cfg);

let visionClient;
try {
  visionClient = createVisionClient(cfg);
} catch {
  visionClient = undefined;
}

const events = new EventBus();
const panels = new PanelState();
const tools = defaultTools({ visionClient, panelState: panels, events });
const queue = new ConfirmationQueue();

startBridge({
  host: cfg.host,
  port: cfg.port,
  client,
  defaultModel: cfg.model,
  tools,
  apiKey: cfg.apiKey,
  ttsVoice: cfg.ttsVoice,
  queue,
  events,
  panels,
});
console.log(`[misty-core] listening on http://${cfg.host}:${cfg.port} (provider=${cfg.provider} model=${cfg.model}, vision=${visionClient?.name ?? "disabled"}, tts_voice=${cfg.ttsVoice})`);

// Graceful shutdown: reject any pending confirmations so the event loop can exit.
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    console.log(`[misty-core] received ${signal}, shutting down`);
    queue.shutdown();
    process.exit(0);
  });
}
