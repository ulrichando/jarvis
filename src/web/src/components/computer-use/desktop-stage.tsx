"use client";
import { type ReactNode, type RefObject } from "react";
import { Hand, Plug, RotateCw } from "lucide-react";
import { NoVNCView, type NoVNCHandle } from "./novnc-view";

export type Status = {
  ready: boolean; streamUp: boolean; sidecarUp: boolean;
  providers?: Record<string, boolean>;
  wsUrl: string; password: string | null; hint: string | null;
};

export function DesktopStage({
  status, connected, takeover, running, novncRef,
  onTakeControl, onGiveControl, onConnect, onRecheck, onVncState,
}: {
  status: Status | null;
  connected: boolean;
  takeover: boolean;
  running: boolean;
  novncRef: RefObject<NoVNCHandle | null>;
  onTakeControl: () => void;
  onGiveControl: () => void;
  onConnect: () => void;
  onRecheck: () => void;
  onVncState: (s: "connecting" | "connected" | "disconnected") => void;
}) {
  return (
    <section className="min-w-0 flex-1 p-3.5">
      <div className={`flex h-full flex-col overflow-hidden rounded-2xl border bg-background shadow-2xl transition-colors ${takeover ? "border-primary" : "border-border/60"}`}>
        <div className="flex h-[34px] shrink-0 items-center gap-2.5 border-b border-border/40 bg-card/40 px-3 text-[11.5px] text-muted-foreground">
          <span className="flex gap-1.5">{[0, 1, 2].map((i) => <span key={i} className="size-2 rounded-full bg-muted-foreground/30" />)}</span>
          <span>Live desktop</span>
        </div>
        <div className="relative flex-1 bg-black/40">
          {status?.ready && status.password && connected ? (
            <>
              <NoVNCView ref={novncRef} wsUrl={status.wsUrl} password={status.password} viewOnly={!takeover} onState={onVncState} className="h-full w-full" />
              <div className="pointer-events-auto absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-2.5 rounded-full border border-border/60 bg-background/80 px-3.5 py-1.5 shadow-xl backdrop-blur">
                {takeover ? (
                  <>
                    <span className="text-[11.5px] text-primary">You&apos;re in control</span>
                    <button onClick={onGiveControl} className="rounded-full bg-primary px-3 py-1 text-[11.5px] font-medium text-primary-foreground">Give control</button>
                  </>
                ) : (
                  <>
                    <span className="inline-flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                      {running ? <><span className="size-2 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />Jarvis is working</> : <>Idle</>}
                    </span>
                    <button onClick={onTakeControl} className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1 text-[11.5px] font-medium text-primary-foreground"><Hand className="size-3" />Take control</button>
                  </>
                )}
              </div>
            </>
          ) : status?.ready && !connected ? (
            <Centered>
              <div className="text-muted-foreground">Disconnected from the desktop.</div>
              <StageBtn onClick={onConnect}><Plug className="size-3.5" /> Connect</StageBtn>
            </Centered>
          ) : (
            <Centered>
              <div className="font-medium text-foreground">Desktop stream not ready</div>
              <ul className="space-y-1 text-xs text-muted-foreground">
                <li className={status?.streamUp ? "text-emerald-500" : ""}>{status?.streamUp ? "✓" : "•"} VNC stream (:6080)</li>
                <li className={status?.sidecarUp ? "text-emerald-500" : ""}>{status?.sidecarUp ? "✓" : "•"} computer-use sidecar (:8771)</li>
              </ul>
              {status?.hint && <pre className="max-w-md overflow-x-auto rounded-md border border-border/60 bg-card/40 p-2 text-[10.5px] leading-5 text-muted-foreground">{status.hint}</pre>}
              <StageBtn onClick={onRecheck}><RotateCw className="size-3.5" /> Re-check</StageBtn>
            </Centered>
          )}
        </div>
      </div>
    </section>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex h-full items-center justify-center"><div className="flex max-w-md flex-col items-center gap-3 rounded-xl border border-border/60 bg-card/40 p-6 text-center text-sm">{children}</div></div>;
}
function StageBtn({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return <button onClick={onClick} className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-card px-3 py-1.5 text-[12px] transition-colors hover:border-border">{children}</button>;
}
