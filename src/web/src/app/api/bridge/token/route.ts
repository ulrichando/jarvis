import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { getOrCreateBridgeToken } from "@/lib/bridge/store";
import { getUserId } from "@/lib/auth-helpers";

// GET /api/bridge/token — the logged-in user's long-lived CLI token. Set it as
// JARVIS_BRIDGE_TOKEN so `jarvis --remote-control` registers its machine under
// THIS account (so /code shows it). Created on first read; stable thereafter.
// Session-authenticated (same-origin from Settings).
export async function GET(req: Request): Promise<NextResponse> {
  try {
    const userId = await getUserId(req.headers);
    const token = getOrCreateBridgeToken(getStore(), userId);
    return NextResponse.json({ token });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return NextResponse.json({ error: msg }, { status: 500 });
  }
}
