"use client";

import { useState, Suspense } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Asterisk, Loader2, ArrowLeft, Mail, KeyRound, ShieldCheck } from "lucide-react";

type Step = "email" | "code" | "password";

function ForgotPasswordForm() {
  const router = useRouter();

  const [step, setStep] = useState<Step>("email");

  // Step 1
  const [email, setEmail] = useState("");
  const [emailBusy, setEmailBusy] = useState(false);

  // Step 2
  const [code, setCode] = useState("");
  const [codeBusy, setCodeBusy] = useState(false);
  const [codeError, setCodeError] = useState<string | null>(null);

  // Step 3 — reset token from step 2
  const [resetToken, setResetToken] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordBusy, setPasswordBusy] = useState(false);

  // ── Step 1: submit email ──────────────────────────────────────────────────
  const submitEmail = async (e: React.FormEvent) => {
    e.preventDefault();
    setEmailBusy(true);
    try {
      await fetch("/api/auth/reset/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim().toLowerCase() }),
      });
    } catch {
      // Swallow — the endpoint is always { ok: true }; network failures
      // shouldn't block the user from trying step 2.
    }
    setEmailBusy(false);
    // Always advance to step 2: anti-enumeration (never reveal account existence)
    setStep("code");
  };

  // ── Step 2: submit code ───────────────────────────────────────────────────
  const submitCode = async (e: React.FormEvent) => {
    e.preventDefault();
    setCodeBusy(true);
    setCodeError(null);
    try {
      const res = await fetch("/api/auth/reset/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          code: code.trim(),
        }),
      });
      if (res.status === 429) {
        setCodeError("Too many attempts. Please wait and try again.");
      } else if (!res.ok) {
        setCodeError("Invalid or expired code.");
      } else {
        const data = (await res.json()) as { token?: string };
        if (data.token) {
          setResetToken(data.token);
          setStep("password");
        } else {
          setCodeError("Invalid or expired code.");
        }
      }
    } catch {
      setCodeError("Network error. Please try again.");
    }
    setCodeBusy(false);
  };

  // ── Step 3: submit new password ───────────────────────────────────────────
  const submitPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    if (password.length < 8) {
      setPasswordError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setPasswordError("Passwords do not match.");
      return;
    }
    setPasswordBusy(true);
    try {
      const res = await fetch("/api/auth/reset/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: resetToken, password }),
      });
      if (!res.ok) {
        // 400 means the token is expired or already used
        setPasswordError("Reset link expired. Please start over.");
        setPasswordBusy(false);
        // Give the user a moment to read the message before resetting
        setTimeout(() => {
          setStep("email");
          setEmail("");
          setCode("");
          setResetToken("");
          setPassword("");
          setConfirm("");
          setPasswordError(null);
        }, 2000);
      } else {
        router.push("/login?reset=1");
      }
    } catch {
      setPasswordError("Network error. Please try again.");
      setPasswordBusy(false);
    }
  };

  // ── Shared input / card styles ────────────────────────────────────────────
  const inputClass =
    "w-full rounded-lg border border-border/60 bg-accent/20 px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40";

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-[380px]">
        {/* Logo */}
        <div className="mb-7 flex items-center justify-center gap-2">
          <Asterisk className="size-6 text-orange-500" strokeWidth={2.5} />
          <span className="font-serif text-[22px] font-bold text-foreground">Jarvis</span>
        </div>

        <div className="rounded-2xl border border-border/60 bg-card p-6">
          {/* ── Step 1: email ──────────────────────────────────────────── */}
          {step === "email" && (
            <>
              <div className="mb-4 flex items-center gap-2">
                <Mail className="size-[18px] text-muted-foreground" />
                <h1 className="text-[18px] font-semibold text-foreground">Reset password</h1>
              </div>
              <p className="mb-5 text-[13px] text-muted-foreground">
                Enter your account email to begin.
              </p>
              <form onSubmit={submitEmail} className="space-y-3">
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
                <button
                  type="submit"
                  disabled={emailBusy || !email}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-[14px] font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40"
                >
                  {emailBusy && <Loader2 className="size-4 animate-spin" />} Continue
                </button>
              </form>
            </>
          )}

          {/* ── Step 2: authenticator code ─────────────────────────────── */}
          {step === "code" && (
            <>
              <div className="mb-4 flex items-center gap-2">
                <KeyRound className="size-[18px] text-muted-foreground" />
                <h1 className="text-[18px] font-semibold text-foreground">Enter a code</h1>
              </div>
              <p className="mb-5 text-[13px] text-muted-foreground">
                If an account with that email has an authenticator set up, enter a code
                from it below. You can also use a backup code.
              </p>
              <form onSubmit={submitCode} className="space-y-3">
                <div>
                  <label className="mb-1 block text-[12px] text-foreground/70">
                    Code
                  </label>
                  <input
                    type="text"
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                    required
                    autoFocus
                    autoComplete="one-time-code"
                    inputMode="numeric"
                    className={inputClass}
                    placeholder="123456 or xxxxx-xxxxx"
                  />
                </div>
                {codeError && (
                  <div className="text-[12.5px] text-red-500">{codeError}</div>
                )}
                <button
                  type="submit"
                  disabled={codeBusy || !code}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-[14px] font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40"
                >
                  {codeBusy && <Loader2 className="size-4 animate-spin" />} Verify
                </button>
              </form>
              <button
                onClick={() => { setCode(""); setCodeError(null); setStep("email"); }}
                className="mt-3 flex items-center gap-1.5 text-[12.5px] text-muted-foreground hover:text-foreground transition-colors"
              >
                <ArrowLeft className="size-3.5" /> Back
              </button>
            </>
          )}

          {/* ── Step 3: new password ────────────────────────────────────── */}
          {step === "password" && (
            <>
              <div className="mb-4 flex items-center gap-2">
                <ShieldCheck className="size-[18px] text-muted-foreground" />
                <h1 className="text-[18px] font-semibold text-foreground">New password</h1>
              </div>
              <p className="mb-5 text-[13px] text-muted-foreground">
                Choose a new password for your account.
              </p>
              <form onSubmit={submitPassword} className="space-y-3">
                <div>
                  <label className="mb-1 block text-[12px] text-foreground/70">Password</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    required
                    autoFocus
                    autoComplete="new-password"
                    className={inputClass}
                    placeholder="••••••••"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-[12px] text-foreground/70">
                    Confirm password
                  </label>
                  <input
                    type="password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    required
                    autoComplete="new-password"
                    className={inputClass}
                    placeholder="••••••••"
                  />
                </div>
                {passwordError && (
                  <div className="text-[12.5px] text-red-500">{passwordError}</div>
                )}
                <button
                  type="submit"
                  disabled={passwordBusy || !password || !confirm}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-[14px] font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40"
                >
                  {passwordBusy && <Loader2 className="size-4 animate-spin" />} Set password
                </button>
              </form>
            </>
          )}
        </div>

        <p className="mt-4 text-center text-[13px] text-muted-foreground">
          Remembered it?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function ForgotPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ForgotPasswordForm />
    </Suspense>
  );
}
