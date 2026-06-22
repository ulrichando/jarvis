"use client";
import type { Part } from "@/lib/computer-use/timeline";

export function PermissionCard({
  part, onApprove,
}: { part: Part; onApprove: (reqId: string, decision: "once" | "session" | "deny") => void }) {
  return (
    <div className="rounded-xl border border-primary bg-primary/10 p-3">
      <div className="text-[13px] text-foreground">
        Allow Jarvis to <span className="font-semibold">{part.label}</span>?
      </div>
      {part.text ? <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">{part.text}</div> : null}
      {part.resolved ? (
        <div className="mt-2 text-[12px] text-muted-foreground">
          {part.resolved === "deny" ? "✗ Denied" : part.resolved === "session" ? "✓ Approved for the session" : "✓ Approved"}
        </div>
      ) : (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <button onClick={() => part.reqId && onApprove(part.reqId, "once")} className="rounded-md bg-primary px-3 py-1.5 text-[11.5px] font-medium text-primary-foreground transition-opacity hover:opacity-90">Approve</button>
          <button onClick={() => part.reqId && onApprove(part.reqId, "session")} className="rounded-md border border-border/60 bg-card px-3 py-1.5 text-[11.5px] text-foreground transition-colors hover:border-border">For session</button>
          <button onClick={() => part.reqId && onApprove(part.reqId, "deny")} className="ml-auto rounded-md border border-border/40 px-3 py-1.5 text-[11.5px] text-destructive transition-colors hover:border-destructive/40">Deny</button>
        </div>
      )}
    </div>
  );
}
