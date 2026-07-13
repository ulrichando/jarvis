"use client";

import { useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Asterisk, Loader2 } from "lucide-react";
import { signIn, authClient } from "@/lib/auth-client";

const INPUT =
  "w-full rounded-lg border border-border/60 bg-accent/20 px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40";
const BTN =
  "flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-[14px] font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/chat";
  const didReset = params.get("reset") === "1";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Two-factor: after email+password on a 2FA account, better-auth returns
  // { data: { twoFactorRedirect: true } } (not an error, not a session) — so we
  // must show a second step for the authenticator code. Without this the page
  // used to just router.push(next) with no real session and bounce to /login
  // (the "sign in does nothing" lockout).
  const [step, setStep] = useState<"creds" | "2fa">("creds");
  const [code, setCode] = useState("");
  const [useBackup, setUseBackup] = useState(false);

  const submitCreds = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { data, error } = await signIn.email({ email, password });
    setBusy(false);
    if (error) {
      setError(error.message ?? "Sign in failed");
      return;
    }
    if (data && (data as { twoFactorRedirect?: boolean }).twoFactorRedirect) {
      setStep("2fa");
      return;
    }
    router.push(next);
  };

  const submit2fa = async (e: React.FormEvent) => {
    e.preventDefault();
    const c = code.trim();
    if (!c) return;
    setBusy(true);
    setError(null);
    const { error } = useBackup
      ? await authClient.twoFactor.verifyBackupCode({ code: c })
      : await authClient.twoFactor.verifyTotp({ code: c });
    setBusy(false);
    if (error) {
      setError(error.message ?? "Invalid code — try again");
      setCode("");
      return;
    }
    router.push(next);
  };

  const backToCreds = () => {
    setStep("creds");
    setCode("");
    setUseBackup(false);
    setError(null);
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-[380px]">
        <div className="mb-7 flex items-center justify-center gap-2">
          <Asterisk className="size-6 text-orange-500" strokeWidth={2.5} />
          <span className="font-serif text-[22px] font-bold text-foreground">Jarvis</span>
        </div>
        {didReset && step === "creds" && (
          <div className="mb-3 rounded-lg border border-green-500/30 bg-green-500/10 px-3 py-2 text-[13px] text-green-600 dark:text-green-400">
            Password reset — please sign in with your new password.
          </div>
        )}

        {step === "creds" ? (
          <>
            <div className="rounded-2xl border border-border/60 bg-card p-6">
              <h1 className="mb-1 text-[18px] font-semibold text-foreground">Welcome back</h1>
              <p className="mb-5 text-[13px] text-muted-foreground">Sign in to continue.</p>
              <form onSubmit={submitCreds} className="space-y-3">
                <div>
                  <label className="mb-1 block text-[12px] text-foreground/70">Email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    autoFocus
                    autoComplete="email"
                    className={INPUT}
                    placeholder="you@example.com"
                  />
                </div>
                <div>
                  <div className="mb-1 flex items-center justify-between">
                    <label className="block text-[12px] text-foreground/70">Password</label>
                    <Link href="/forgot-password" className="text-[12px] text-muted-foreground hover:text-primary transition-colors">
                      Forgot password?
                    </Link>
                  </div>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoComplete="current-password"
                    className={INPUT}
                    placeholder="••••••••"
                  />
                </div>
                {error && <div className="text-[12.5px] text-red-500">{error}</div>}
                <button type="submit" disabled={busy || !email || !password} className={BTN}>
                  {busy && <Loader2 className="size-4 animate-spin" />} Sign in
                </button>
              </form>
            </div>
            <p className="mt-4 text-center text-[13px] text-muted-foreground">
              No account?{" "}
              <Link href="/signup" className="text-primary hover:underline">
                Create one
              </Link>
            </p>
          </>
        ) : (
          <>
            <div className="rounded-2xl border border-border/60 bg-card p-6">
              <h1 className="mb-1 text-[18px] font-semibold text-foreground">Two-factor authentication</h1>
              <p className="mb-5 text-[13px] text-muted-foreground">
                {useBackup
                  ? "Enter one of your backup codes."
                  : "Enter the 6-digit code from your authenticator app."}
              </p>
              <form onSubmit={submit2fa} className="space-y-3">
                <input
                  type="text"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  required
                  autoFocus
                  inputMode={useBackup ? "text" : "numeric"}
                  autoComplete="one-time-code"
                  maxLength={useBackup ? 24 : 8}
                  className={`${INPUT} text-center tracking-[0.3em]`}
                  placeholder={useBackup ? "backup code" : "123456"}
                />
                {error && <div className="text-[12.5px] text-red-500">{error}</div>}
                <button type="submit" disabled={busy || !code.trim()} className={BTN}>
                  {busy && <Loader2 className="size-4 animate-spin" />} Verify
                </button>
              </form>
              <div className="mt-4 flex items-center justify-between text-[12px]">
                <button
                  type="button"
                  onClick={backToCreds}
                  className="text-muted-foreground hover:text-foreground transition-colors"
                >
                  ← Back
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setUseBackup((v) => !v);
                    setCode("");
                    setError(null);
                  }}
                  className="text-muted-foreground hover:text-primary transition-colors"
                >
                  {useBackup ? "Use authenticator code" : "Use a backup code instead"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
