"use client";

import { useState, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Asterisk, Loader2, ShieldCheck } from "lucide-react";
import { authClient, signIn } from "@/lib/auth-client";

// Sign-in is two-step for users with an enrolled authenticator: better-auth's
// two-factor plugin answers a correct email+password with
// `{ twoFactorRedirect: true }` and NO session (a short-lived signed 2FA
// cookie carries the pending challenge, ~10 min). Without the challenge step
// below, an enrolled user could never complete sign-in — the app redirected
// them straight back to /login in a loop.
type Step = "credentials" | "totp" | "backup";

const inputClass =
  "w-full rounded-lg border border-border/60 bg-accent/20 px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/chat";
  const didReset = params.get("reset") === "1";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [step, setStep] = useState<Step>("credentials");
  const [code, setCode] = useState("");
  const [trustDevice, setTrustDevice] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { data, error } = await signIn.email({ email, password });
    setBusy(false);
    if (error) {
      setError(error.message ?? "Sign in failed");
      return;
    }
    // Enrolled-2FA account: no session yet — show the challenge step.
    if ((data as { twoFactorRedirect?: boolean } | null)?.twoFactorRedirect) {
      setCode("");
      setStep("totp");
      return;
    }
    router.push(next);
  };

  const backToSignIn = () => {
    setStep("credentials");
    setCode("");
    setPassword("");
    setError(null);
  };

  const verify = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error } =
      step === "backup"
        ? await authClient.twoFactor.verifyBackupCode({
            code: code.trim(),
            trustDevice,
          })
        : await authClient.twoFactor.verifyTotp({
            code: code.trim(),
            trustDevice,
          });
    setBusy(false);
    if (error) {
      // The pending-challenge cookie lasts ~10 minutes. Once it's gone the
      // server can no longer tie the code to a user — restart from step 1.
      if (error.code === "INVALID_TWO_FACTOR_COOKIE") {
        backToSignIn();
        setError("Your verification window expired — please sign in again.");
        return;
      }
      setError(
        step === "backup"
          ? "That backup code isn't valid. Each code works only once — try another."
          : "That code didn't work. Check your authenticator app and try again.",
      );
      setCode("");
      return;
    }
    router.push(next);
  };

  const switchMethod = (to: Step) => {
    setStep(to);
    setCode("");
    setError(null);
  };

  const codeValid =
    step === "backup" ? code.trim().length > 0 : code.trim().length === 6;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-[380px]">
        <div className="mb-7 flex items-center justify-center gap-2">
          <Asterisk className="size-6 text-orange-500" strokeWidth={2.5} />
          <span className="font-serif text-[22px] font-bold text-foreground">Jarvis</span>
        </div>
        {didReset && step === "credentials" && (
          <div className="mb-3 rounded-lg border border-green-500/30 bg-green-500/10 px-3 py-2 text-[13px] text-green-600 dark:text-green-400">
            Password reset — please sign in with your new password.
          </div>
        )}
        <div className="rounded-2xl border border-border/60 bg-card p-6">
          {step === "credentials" ? (
            <>
              <h1 className="mb-1 text-[18px] font-semibold text-foreground">Welcome back</h1>
              <p className="mb-5 text-[13px] text-muted-foreground">Sign in to continue.</p>
              <form onSubmit={submit} className="space-y-3">
                <div>
                  <label className="mb-1 block text-[12px] text-foreground/70">Email</label>
                  <input
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    required
                    autoFocus
                    autoComplete="email"
                    className={inputClass}
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
                    className={inputClass}
                    placeholder="••••••••"
                  />
                </div>
                {error && <div className="text-[12.5px] text-red-500">{error}</div>}
                <button
                  type="submit"
                  disabled={busy || !email || !password}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-[14px] font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40"
                >
                  {busy && <Loader2 className="size-4 animate-spin" />} Sign in
                </button>
              </form>
            </>
          ) : (
            <>
              <div className="mb-1 flex items-center gap-2">
                <ShieldCheck className="size-4 text-primary" />
                <h1 className="text-[18px] font-semibold text-foreground">Two-factor authentication</h1>
              </div>
              <p className="mb-5 text-[13px] text-muted-foreground">
                {step === "backup"
                  ? "Enter one of your saved backup codes."
                  : "Enter the 6-digit code from your authenticator app."}
              </p>
              <form onSubmit={verify} className="space-y-3">
                <div>
                  <label className="mb-1 block text-[12px] text-foreground/70">
                    {step === "backup" ? "Backup code" : "Verification code"}
                  </label>
                  {step === "backup" ? (
                    <input
                      type="text"
                      value={code}
                      onChange={(e) => setCode(e.target.value)}
                      required
                      autoFocus
                      autoComplete="off"
                      spellCheck={false}
                      className={`${inputClass} font-mono tracking-wider`}
                      placeholder="Backup code"
                    />
                  ) : (
                    <input
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]*"
                      maxLength={6}
                      value={code}
                      onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                      required
                      autoFocus
                      autoComplete="one-time-code"
                      className={`${inputClass} text-center font-mono text-[16px] tracking-widest`}
                      placeholder="000000"
                    />
                  )}
                </div>
                <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-foreground/70 select-none">
                  <input
                    type="checkbox"
                    checked={trustDevice}
                    onChange={(e) => setTrustDevice(e.target.checked)}
                    className="size-3.5 accent-[var(--primary)]"
                  />
                  Trust this device for 30 days
                </label>
                {error && <div className="text-[12.5px] text-red-500">{error}</div>}
                <button
                  type="submit"
                  disabled={busy || !codeValid}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-[14px] font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40"
                >
                  {busy && <Loader2 className="size-4 animate-spin" />} Verify
                </button>
              </form>
              <div className="mt-4 flex items-center justify-between text-[12.5px]">
                <button
                  type="button"
                  onClick={() => switchMethod(step === "backup" ? "totp" : "backup")}
                  className="text-muted-foreground hover:text-primary transition-colors"
                >
                  {step === "backup" ? "Use authenticator code" : "Use a backup code"}
                </button>
                <button
                  type="button"
                  onClick={backToSignIn}
                  className="text-muted-foreground hover:text-primary transition-colors"
                >
                  Back to sign in
                </button>
              </div>
            </>
          )}
        </div>
        {step === "credentials" && (
          <p className="mt-4 text-center text-[13px] text-muted-foreground">
            No account?{" "}
            <Link href="/signup" className="text-primary hover:underline">
              Create one
            </Link>
          </p>
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
