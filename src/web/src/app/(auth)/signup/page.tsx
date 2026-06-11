"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Asterisk, Loader2 } from "lucide-react";
import { signUp } from "@/lib/auth-client";

export default function SignupPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setBusy(true);
    setError(null);
    const { error } = await signUp.email({ email, password, name });
    setBusy(false);
    if (error) setError(error.message ?? "Sign up failed");
    else router.push("/chat");
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-[380px]">
        <div className="mb-7 flex items-center justify-center gap-2">
          <Asterisk className="size-6 text-orange-500" strokeWidth={2.5} />
          <span className="font-serif text-[22px] font-bold text-foreground">Jarvis</span>
        </div>
        <div className="rounded-2xl border border-border/60 bg-card p-6">
          <h1 className="mb-1 text-[18px] font-semibold text-foreground">Create your account</h1>
          <p className="mb-5 text-[13px] text-muted-foreground">Get started with Jarvis.</p>
          <form onSubmit={submit} className="space-y-3">
            <div>
              <label className="mb-1 block text-[12px] text-foreground/70">Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                autoFocus
                autoComplete="name"
                className="w-full rounded-lg border border-border/60 bg-accent/20 px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40"
                placeholder="Ulrich"
              />
            </div>
            <div>
              <label className="mb-1 block text-[12px] text-foreground/70">Email</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                className="w-full rounded-lg border border-border/60 bg-accent/20 px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40"
                placeholder="you@example.com"
              />
            </div>
            <div>
              <label className="mb-1 block text-[12px] text-foreground/70">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="new-password"
                className="w-full rounded-lg border border-border/60 bg-accent/20 px-3 py-2 text-[14px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/40"
                placeholder="At least 8 characters"
              />
            </div>
            {error && <div className="text-[12.5px] text-red-500">{error}</div>}
            <button
              type="submit"
              disabled={busy || !email || !password || !name}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-[14px] font-medium text-primary-foreground hover:bg-primary/90 transition-colors disabled:opacity-40"
            >
              {busy && <Loader2 className="size-4 animate-spin" />} Create account
            </button>
          </form>
        </div>
        <p className="mt-4 text-center text-[13px] text-muted-foreground">
          Already have an account?{" "}
          <Link href="/login" className="text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
