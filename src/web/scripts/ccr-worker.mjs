#!/usr/bin/env node
/**
 * Thin CCR worker for JARVIS /code.
 *
 * This is the *worker* half of the /code flow: it registers with the
 * self-hosted web CCR server (the routes under /api/bridge/v1/*), long-polls
 * for tasks dispatched from /code, executes each via an LLM, and streams the
 * result back as session events that the /code session view renders live.
 *
 * The production worker is `jarvis /remote-control`; this script speaks the
 * same web protocol without the CLI's claude.ai-specific auth/protocol gates,
 * so the end-to-end flow can be exercised today.
 *
 *   BASE              JARVIS_BRIDGE_BASE_URL  (default http://localhost:3000/api/bridge)
 *   GROQ_API_KEY      executor key (OpenAI-compatible)
 *   WORKER_NAME       machine name shown in /code   (default hostname)
 *   WORKER_DIR        working directory             (default cwd)
 */

import os from "node:os";

const BASE = process.env.JARVIS_BRIDGE_BASE_URL || "http://localhost:3000/api/bridge";
const ANTHROPIC_KEY = process.env.ANTHROPIC_API_KEY;
const GROQ_KEY = process.env.GROQ_API_KEY;
const MODEL = process.env.WORKER_MODEL || "openai/gpt-oss-120b";
const ANTHROPIC_MODEL = "claude-haiku-4-5-20251001";
const MACHINE = process.env.WORKER_NAME || os.hostname();
const DIR = process.env.WORKER_DIR || process.cwd();

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function register() {
  const r = await fetch(`${BASE}/v1/environments/bridge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      machine_name: MACHINE,
      directory: DIR,
      branch: "master",
      git_repo_url: "https://github.com/ulrichando/jarvis",
      max_sessions: 2,
      metadata: { worker_type: "jarvis" },
    }),
  });
  if (!r.ok) throw new Error(`register failed ${r.status}: ${await r.text()}`);
  return r.json(); // { environment_id, environment_secret }
}

async function poll(envId, secret) {
  const r = await fetch(`${BASE}/v1/environments/${envId}/work/poll`, {
    headers: { Authorization: `Bearer ${secret}` },
  });
  if (!r.ok) throw new Error(`poll ${r.status}`);
  return r.json(); // null OR { id, data:{ id:sessionId, prompt }, ... }
}

async function emit(sessionId, secret, type, payload) {
  await fetch(`${BASE}/v1/sessions/${sessionId}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${secret}` },
    body: JSON.stringify({ events: [{ type, ...payload }] }),
  }).catch((e) => log("emit error", String(e)));
}

async function ack(envId, workId, secret) {
  await fetch(`${BASE}/v1/environments/${envId}/work/${workId}/ack`, {
    method: "POST",
    headers: { Authorization: `Bearer ${secret}` },
  }).catch(() => {});
}

const SYSTEM = `You are a coding agent working in the repo at ${DIR} on machine ${MACHINE}. Answer concisely.`;

async function execute(prompt) {
  if (ANTHROPIC_KEY) {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: ANTHROPIC_MODEL,
        max_tokens: 1024,
        system: SYSTEM,
        messages: [{ role: "user", content: prompt }],
      }),
    });
    if (!r.ok) return `Executor error ${r.status}: ${(await r.text()).slice(0, 300)}`;
    const j = await r.json();
    return (j.content ?? []).map((c) => c.text).filter(Boolean).join("") || JSON.stringify(j).slice(0, 500);
  }
  if (GROQ_KEY) {
    const r = await fetch("https://api.groq.com/openai/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${GROQ_KEY}` },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 1024,
        messages: [
          { role: "system", content: SYSTEM },
          { role: "user", content: prompt },
        ],
      }),
    });
    if (!r.ok) return `Executor error ${r.status}: ${(await r.text()).slice(0, 300)}`;
    const j = await r.json();
    return j.choices?.[0]?.message?.content ?? JSON.stringify(j).slice(0, 500);
  }
  return `(no LLM key — echo) ${prompt}`;
}

async function main() {
  const executor = ANTHROPIC_KEY ? ANTHROPIC_MODEL : GROQ_KEY ? MODEL : "echo";
  log(`worker "${MACHINE}" → ${BASE}  (executor: ${executor})`);
  const env = await register();
  log(`registered env ${env.environment_id}`);
  let idle = 0;
  for (;;) {
    let work;
    try {
      work = await poll(env.environment_id, env.environment_secret);
    } catch (e) {
      log("poll error, retrying in 2s:", String(e));
      await new Promise((r) => setTimeout(r, 2000));
      continue;
    }
    if (!work) {
      if (++idle % 4 === 0) log("…idle (long-polling)");
      continue;
    }
    idle = 0;
    const sessionId = work.data?.id;
    const prompt = work.data?.prompt ?? "";
    log(`work ${work.id}: session=${sessionId} prompt=${JSON.stringify(prompt).slice(0, 80)}`);
    if (!sessionId) {
      log("  no session id in work.data, skipping");
      continue;
    }
    await emit(sessionId, env.environment_secret, "status", { status: "Working on it…" });
    let out;
    try {
      out = await execute(prompt);
    } catch (e) {
      out = `Execution failed: ${String(e)}`;
    }
    await emit(sessionId, env.environment_secret, "assistant", { text: out });
    await emit(sessionId, env.environment_secret, "status", { status: "Done" });
    await ack(env.environment_id, work.id, env.environment_secret);
    log(`  done ${work.id} (${out.length} chars)`);
  }
}

main().catch((e) => {
  console.error("worker fatal:", e);
  process.exit(1);
});
