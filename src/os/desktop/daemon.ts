// misty-core daemon entry point. Wires config + HTTP bridge.
import { startBridge } from "./bridge/server.ts";

const host = process.env.MISTY_HOST ?? "127.0.0.1";
const port = Number(process.env.MISTY_PORT ?? 8765);

startBridge({ host, port });
console.log(`[misty-core] listening on http://${host}:${port}`);
