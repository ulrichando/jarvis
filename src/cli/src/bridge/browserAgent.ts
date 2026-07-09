// browserAgent.ts — the "chat that acts" loop for Jarvis in Chrome.
//
// Runs a tool-use loop against the LLM proxy: the model reads the page and
// drives it through the extension's existing command surface (the same
// ext_browse actions the voice agent uses). Destructive steps are gated — in
// "ask" mode the extension's safety gate returns needs_confirmation and we ask
// the user before re-sending confirmed. Every step is streamed to the panel via
// onEvent so the transcript shows what Jarvis is doing.

import { TOOLS, SYSTEM } from './browserTools.js'

export type AgentEvent =
  | { type: "text"; text: string }
  | { type: "action"; action: string; label: string }
  | { type: "action_result"; ok: boolean; summary: string }
  | { type: "approval"; approvalId: string; label: string };

export interface RunAgentOpts {
  text: string;
  model: string;
  proxyUrl: string;
  headers: Record<string, string>;
  mode: "ask" | "auto";
  sendCommand: (action: string, args: any, confirmed: boolean) => Promise<any>;
  requestApproval: (label: string) => Promise<boolean>;
  onEvent: (ev: AgentEvent) => void;
  maxSteps?: number;
}

function labelFor(action: string, args: any): string {
  const a = args || {};
  switch (action) {
    case "navigate": return `Navigate to ${a.url}`;
    case "click": return `Click ${a.selector}`;
    case "click_text": return `Click "${a.text}"`;
    case "type": return `Type into ${a.selector}`;
    case "fill_form": return `Fill ${Object.keys(a.fields || {}).length} field(s)`;
    case "submit": return `Submit form ${a.form_selector || a.selector || ""}`.trim();
    case "select": return `Select "${a.value}" in ${a.selector}`;
    case "scroll": return `Scroll ${a.direction || a.selector || "down"}`;
    case "press_key": return `Press ${a.key}`;
    case "wait_for": return `Wait for ${a.selector}`;
    case "dom_summary": return "Read the page";
    case "extract_text": return a.selector ? `Read ${a.selector}` : "Read the page text";
    case "find_by_text": return `Find "${a.text}"`;
    case "read_console": return "Read the console";
    case "read_network": return "Read network requests";
    case "get_url": return "Check the current URL";
    case "back": return "Go back";
    case "forward": return "Go forward";
    case "list_tabs": return "List open tabs";
    case "activate_tab": return `Switch to tab ${a.tab_id}`;
    case "group_tabs": return a.tab_ids && a.tab_ids.length ? `Group ${a.tab_ids.length} tabs` : "Group this tab";
    case "download": return `Download ${a.url}`;
    default: return action;
  }
}

// Cap the size of a tool result fed back to the model (dom_summary/extract_text
// can be large); keep it useful but bounded.
function summarizeResult(action: string, result: any): string {
  if (!result || result.ok === false) return `error: ${result?.error || "failed"}`;
  const s = JSON.stringify(result);
  return s.length > 4000 ? s.slice(0, 4000) + "…(truncated)" : s;
}

export async function runBrowserAgent(opts: RunAgentOpts): Promise<string> {
  const maxSteps = opts.maxSteps ?? 16;
  const messages: any[] = [{ role: "user", content: opts.text }];
  let finalText = "";

  for (let step = 0; step < maxSteps; step++) {
    let data: any;
    try {
      const resp = await fetch(`${opts.proxyUrl}/v1/messages`, {
        method: "POST",
        headers: opts.headers,
        body: JSON.stringify({
          model: opts.model,
          max_tokens: 1500,
          system: SYSTEM,
          messages,
          tools: TOOLS,
        }),
        signal: AbortSignal.timeout(45_000),
      });
      data = await resp.json();
    } catch (e: any) {
      return `Error reaching the model: ${e?.message || e}`;
    }
    if (data?.error) return `Error: ${data.error.message || JSON.stringify(data.error)}`;
    const content: any[] = Array.isArray(data?.content) ? data.content : [];

    const texts = content.filter((b) => b.type === "text").map((b) => b.text).filter(Boolean);
    if (texts.length) { finalText = texts.join("\n"); opts.onEvent({ type: "text", text: finalText }); }

    const toolUses = content.filter((b) => b.type === "tool_use");
    if (toolUses.length === 0) return finalText || "(done)";

    messages.push({ role: "assistant", content });
    const toolResults: any[] = [];
    for (const tu of toolUses) {
      const action = tu.name;
      const args = tu.input || {};
      // wait_for must resolve within the per-command timeout (15s in server.ts)
      // or the command aborts before the wait does — clamp under that budget.
      if (action === "wait_for" && typeof args.timeout_ms === "number") {
        args.timeout_ms = Math.min(Math.max(args.timeout_ms, 0), 12_000);
      }
      const label = labelFor(action, args);
      opts.onEvent({ type: "action", action, label });

      let result: any;
      try {
        // Background actions (download) bypass the content-script safety gate, so
        // gate the sensitive one here: download writes a file to disk, so ask
        // first in ask mode. Otherwise: auto mode confirms up front; ask mode
        // sends unconfirmed and lets the element-aware safety gate decide.
        const preConfirm = opts.mode === "ask" && action === "download";
        if (preConfirm && !(await opts.requestApproval(label))) {
          result = { ok: false, error: "denied by user" };
        } else {
          result = await opts.sendCommand(action, args, opts.mode === "auto" || preConfirm);
          if (opts.mode === "ask" && result && result.needs_confirmation) {
            const approved = await opts.requestApproval(label);
            result = approved
              ? await opts.sendCommand(action, args, true)
              : { ok: false, error: "denied by user" };
          }
        }
      } catch (e: any) {
        result = { ok: false, error: String(e?.message || e) };
      }
      opts.onEvent({ type: "action_result", ok: result?.ok !== false, summary: label });
      toolResults.push({ type: "tool_result", tool_use_id: tu.id, content: summarizeResult(action, result) });
    }
    messages.push({ role: "user", content: toolResults });
  }
  return finalText || "I reached the step limit before finishing — want me to keep going?";
}

export const _AGENT_TOOLS = TOOLS; // for tests
