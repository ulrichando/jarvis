import { NextResponse } from "next/server";
import { getStore } from "@/lib/bridge/db";
import { runAutofixTick } from "@/lib/bridge/autofix";

// POST /api/bridge/v1/code/autofix/tick — run one auto-fix-CI pass. Called by
// the in-process interval (instrumentation.ts) and available for a systemd
// timer. Localhost, idempotent per failing commit.
export async function POST(): Promise<NextResponse> {
  try {
    const fixed = await runAutofixTick(getStore());
    return NextResponse.json({ fixed });
  } catch (err) {
    return NextResponse.json({ fixed: 0, error: String(err) }, { status: 500 });
  }
}
