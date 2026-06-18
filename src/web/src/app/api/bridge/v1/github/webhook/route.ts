import { NextResponse } from "next/server";
import { createHmac, timingSafeEqual } from "node:crypto";
import { getStore } from "@/lib/bridge/db";
import { getGithubWebhookSecret } from "@/lib/connectors/github";
import { runAutofixTick } from "@/lib/bridge/autofix";
import { runGithubRoutines } from "@/lib/bridge/routines-tick";

// POST /api/bridge/v1/github/webhook — receive GitHub App / repo webhooks.
// Makes auto-fix-CI instant (vs. the 90s poll) and is the trigger for
// github-event routines. Requires a webhook secret (GITHUB_WEBHOOK_SECRET or
// the connector's webhookSecret); deliveries are HMAC-verified.
//
// Point a GitHub webhook (or the Claude/Jarvis GitHub App) at this URL — needs
// the web app reachable from GitHub (a tunnel, since it binds 127.0.0.1).
function verify(secret: string, body: string, sig: string | null): boolean {
  if (!sig) return false;
  const expected = "sha256=" + createHmac("sha256", secret).update(body).digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(sig);
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function POST(req: Request): Promise<NextResponse> {
  const secret = await getGithubWebhookSecret();
  if (!secret) {
    return NextResponse.json(
      { error: "GitHub webhook secret not configured" },
      { status: 503 },
    );
  }
  const raw = await req.text();
  if (!verify(secret, raw, req.headers.get("x-hub-signature-256"))) {
    return NextResponse.json({ error: "bad signature" }, { status: 401 });
  }
  const event = req.headers.get("x-github-event") ?? "";
  let payload: Record<string, unknown> = {};
  try {
    payload = JSON.parse(raw) as Record<string, unknown>;
  } catch {
    /* some events have no body */
  }
  const store = getStore();
  const origin = new URL(req.url).origin;

  let autofixed = 0;
  let routinesRun = 0;
  try {
    // CI signal → run an auto-fix pass (it finds the matching autofix session).
    if (["check_run", "check_suite", "workflow_run", "status"].includes(event)) {
      const concl =
        ((payload.check_run ?? payload.check_suite ?? payload.workflow_run) as
          | { conclusion?: string }
          | undefined)?.conclusion ?? payload.state;
      if (concl === "failure" || concl === "timed_out" || concl === "error") {
        autofixed = await runAutofixTick(store);
      }
    }
    // Auto code-review on PR open (opt-in via JARVIS_CODE_AUTO_REVIEW=1).
    if (
      event === "pull_request" &&
      process.env.JARVIS_CODE_AUTO_REVIEW === "1" &&
      ["opened", "reopened"].includes(String(payload.action ?? ""))
    ) {
      const repo = (payload.repository as { full_name?: string } | undefined)?.full_name;
      const number = (payload.pull_request as { number?: number } | undefined)?.number;
      if (repo && typeof number === "number") {
        const { reviewPullRequest } = await import("@/lib/bridge/code-review");
        void reviewPullRequest(repo, number).catch(() => {});
      }
    }
    // Any event → fire github-trigger routines subscribed to it (filters applied).
    if (event) routinesRun = await runGithubRoutines(store, origin, event, payload);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
  return NextResponse.json({ ok: true, event, autofixed, routinesRun });
}
