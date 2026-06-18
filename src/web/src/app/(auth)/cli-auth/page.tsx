"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Asterisk, Loader2, ShieldCheck, Terminal } from "lucide-react";
import { useSession } from "@/lib/auth-client";

/**
 * Device-authorization handoff for the `jarvis` CLI's /login command.
 *
 * The CLI starts a localhost loopback listener and opens the browser here with
 * ?redirect_uri=http://localhost:PORT/callback&state=RANDOM. Once the user is
 * signed in and approves, we mint their long-lived Remote Control token
 * (/api/bridge/token, session-authed) and hand it back by navigating the
 * browser to the loopback redirect with ?code=TOKEN&state=STATE. The CLI
 * verifies the state, persists the token, and serves its own success page.
 *
 * redirect_uri is restricted to loopback hosts so the token can never be
 * exfiltrated to a remote origin, and approval is an explicit click (no
 * drive-by grants).
 */

function isLoopbackCallback(raw: string | null): raw is string {
  if (!raw) return false;
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return false;
  }
  return (
    u.protocol === "http:" &&
    (u.hostname === "localhost" ||
      u.hostname === "127.0.0.1" ||
      u.hostname === "::1") &&
    u.pathname === "/callback"
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-[380px]">
        <div className="mb-7 flex items-center justify-center gap-2">
          <Asterisk className="size-6 text-orange-500" strokeWidth={2.5} />
          <span className="font-serif text-[22px] font-bold text-foreground">
            Jarvis
          </span>
        </div>
        <div className="rounded-2xl border border-border/60 bg-card p-6">
          {children}
        </div>
      </div>
    </div>
  );
}

function CliAuth() {
  const router = useRouter();
  const params = useSearchParams();
  const { data: session, isPending } = useSession();

  const redirectUri = params.get("redirect_uri");
  const state = params.get("state");
  const validTarget = useMemo(
    () => isLoopbackCallback(redirectUri) && !!state,
    [redirectUri, state],
  );

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Bounce unauthenticated users to sign-in, returning here afterwards.
  useEffect(() => {
    if (isPending || session) return;
    const here = `/cli-auth?redirect_uri=${encodeURIComponent(
      redirectUri ?? "",
    )}&state=${encodeURIComponent(state ?? "")}`;
    router.replace(`/login?next=${encodeURIComponent(here)}`);
  }, [isPending, session, redirectUri, state, router]);

  const approve = async () => {
    if (!validTarget) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/bridge/token", { credentials: "same-origin" });
      if (!res.ok) {
        throw new Error(`Could not mint a Remote Control token (HTTP ${res.status}).`);
      }
      const { token } = (await res.json()) as { token?: string };
      if (!token) throw new Error("The server returned no Remote Control token.");
      // Hand the token to the CLI's loopback listener. The CLI serves its own
      // success page from here, so this tab navigates away.
      window.location.href = `${redirectUri}?code=${encodeURIComponent(
        token,
      )}&state=${encodeURIComponent(state!)}`;
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : "Authorization failed.");
    }
  };

  if (isPending || !session) {
    return (
      <Shell>
        <div className="flex items-center justify-center gap-2 py-4 text-[13px] text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading…
        </div>
      </Shell>
    );
  }

  if (!validTarget) {
    return (
      <Shell>
        <h1 className="mb-1 text-[18px] font-semibold text-foreground">
          Invalid sign-in link
        </h1>
        <p className="text-[13px] text-muted-foreground">
          This page is opened by the <code>jarvis</code> CLI. Run{" "}
          <code>/login</code> inside a <code>jarvis</code> session (or{" "}
          <code>jarvis auth login</code>) to start.
        </p>
      </Shell>
    );
  }

  const email = session.user?.email ?? "your account";

  return (
    <Shell>
      <div className="mb-4 flex items-center gap-2 text-foreground">
        <Terminal className="size-4 text-orange-500" />
        <h1 className="text-[18px] font-semibold">Authorize the Jarvis CLI</h1>
      </div>
      <p className="mb-5 text-[13px] text-muted-foreground">
        Connect the <code>jarvis</code> command-line tool on this device to{" "}
        <span className="text-foreground">{email}</span>. It will receive a
        Remote Control token to run sessions under your account.
      </p>
      {error && <div className="mb-3 text-[12.5px] text-red-500">{error}</div>}
      <button
        type="button"
        onClick={approve}
        disabled={busy}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-[14px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-40"
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <ShieldCheck className="size-4" />
        )}
        Authorize this device
      </button>
      <p className="mt-4 text-center text-[12px] text-muted-foreground">
        Only approve if you just ran <code>/login</code> in your terminal.
      </p>
    </Shell>
  );
}

export default function CliAuthPage() {
  return (
    <Suspense fallback={null}>
      <CliAuth />
    </Suspense>
  );
}
