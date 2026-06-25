"use client";

import { useEffect, useRef, useState } from "react";
import { ShieldCheck, KeyRound, Smartphone, Copy, Check, RefreshCw, AlertTriangle } from "lucide-react";
import QRCode from "qrcode";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { authClient, useSession } from "@/lib/auth-client";

// ─── helpers ──────────────────────────────────────────────────────────────────

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="mb-5">
      <h2 className="text-[17px] font-semibold">{title}</h2>
      <div className="mt-2 border-t border-border/60" />
    </div>
  );
}

/** Extract the bare base-32 secret from an otpauth:// URI. */
function secretFromUri(uri: string): string {
  try {
    const params = new URL(uri).searchParams;
    return params.get("secret") ?? "";
  } catch {
    return "";
  }
}

// ─── CopyButton ───────────────────────────────────────────────────────────────

function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const doCopy = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      type="button"
      onClick={doCopy}
      className="inline-flex items-center gap-1.5 text-[13px] text-primary hover:text-primary/80 transition-colors"
    >
      {copied ? (
        <Check className="size-3.5 text-green-500" />
      ) : (
        <Copy className="size-3.5" />
      )}
      {copied ? "Copied" : label}
    </button>
  );
}

// ─── QrCanvas ─────────────────────────────────────────────────────────────────

function QrCanvas({ uri }: { uri: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!canvasRef.current || !uri) return;
    QRCode.toCanvas(canvasRef.current, uri, {
      width: 180,
      margin: 2,
      color: { dark: "#000000", light: "#ffffff" },
    }).catch(() => {
      /* non-fatal — user can use the manual secret */
    });
  }, [uri]);

  return (
    <canvas
      ref={canvasRef}
      className="rounded-lg border border-border/60 bg-white"
      aria-label="TOTP QR code — scan with your authenticator app"
    />
  );
}

// ─── PasswordPrompt ──────────────────────────────────────────────────────────

function PasswordPrompt({
  label,
  buttonLabel,
  pending,
  onSubmit,
}: {
  label: string;
  buttonLabel: string;
  pending: boolean;
  onSubmit: (password: string) => void;
}) {
  const [password, setPassword] = useState("");
  return (
    <div className="flex flex-col gap-3">
      <div>
        <label className="block text-[14px] font-medium mb-1.5">{label}</label>
        <div className="flex gap-2">
          <Input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Your current password"
            className="max-w-xs"
            onKeyDown={(e) => {
              if (e.key === "Enter" && password.trim()) onSubmit(password);
            }}
          />
          <Button
            onClick={() => onSubmit(password)}
            disabled={pending || !password.trim()}
          >
            {pending ? "Please wait…" : buttonLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ─── BackupCodesDisplay ───────────────────────────────────────────────────────

function BackupCodesDisplay({
  codes,
  onConfirmed,
}: {
  codes: string[];
  onConfirmed: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const allCodes = codes.join("\n");

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4">
        <div className="flex items-start gap-2">
          <AlertTriangle className="size-4 text-amber-500 mt-0.5 shrink-0" />
          <p className="text-[13px] text-amber-700 dark:text-amber-400 leading-relaxed">
            Save these backup codes now — they&apos;re shown only once and are
            your only recovery option if you lose access to your authenticator
            app. Store them somewhere safe (e.g. a password manager).
          </p>
        </div>
      </div>

      <div className="rounded-xl border border-border/60 bg-muted/40 p-4">
        <div className="grid grid-cols-2 gap-x-8 gap-y-1.5">
          {codes.map((code) => (
            <span key={code} className="font-mono text-[13px] tracking-wider text-foreground">
              {code}
            </span>
          ))}
        </div>
        <div className="mt-3 flex justify-end">
          <CopyButton value={allCodes} label="Copy all codes" />
        </div>
      </div>

      <div className="flex items-center gap-3 pt-1">
        <button
          type="button"
          onClick={() => setConfirmed((v) => !v)}
          className={cn(
            "flex size-5 shrink-0 items-center justify-center rounded border-2 transition-colors",
            confirmed
              ? "border-primary bg-primary text-primary-foreground"
              : "border-border/60 bg-transparent",
          )}
          aria-checked={confirmed}
          role="checkbox"
        >
          {confirmed && <Check className="size-3" />}
        </button>
        <span
          className="text-[13px] text-foreground/80 cursor-pointer select-none"
          onClick={() => setConfirmed((v) => !v)}
        >
          I&apos;ve saved my backup codes in a safe place
        </span>
      </div>

      <div className="flex justify-end pt-1">
        <Button onClick={onConfirmed} disabled={!confirmed}>
          Continue to verification →
        </Button>
      </div>
    </div>
  );
}

// ─── EnrollFlow ───────────────────────────────────────────────────────────────

type EnrollStep =
  | "password"
  | "qr"
  | "backup-codes"
  | "verify"
  | "done";

interface EnrollData {
  totpURI: string;
  backupCodes: string[];
}

function EnrollFlow({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<EnrollStep>("password");
  const [pending, setPending] = useState(false);
  const [enrollData, setEnrollData] = useState<EnrollData | null>(null);
  const [code, setCode] = useState("");

  const handleEnable = async (password: string) => {
    setPending(true);
    try {
      const result = await authClient.twoFactor.enable({ password });
      if (result.error) {
        toast.error(result.error.message ?? "Failed to enable authenticator");
        return;
      }
      // result.data has shape { totpURI: string; backupCodes: string[] }
      const data = result.data as EnrollData;
      setEnrollData(data);
      setStep("qr");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to enable authenticator");
    } finally {
      setPending(false);
    }
  };

  const handleVerify = async () => {
    if (!code.trim()) return;
    setPending(true);
    try {
      const result = await authClient.twoFactor.verifyTotp({ code: code.trim() });
      if (result.error) {
        toast.error(result.error.message ?? "Invalid code — try again");
        setCode("");
        return;
      }
      toast.success("Authenticator enrolled");
      setStep("done");
      onDone();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Verification failed");
    } finally {
      setPending(false);
    }
  };

  if (step === "password") {
    return (
      <PasswordPrompt
        label="Enter your current password to continue"
        buttonLabel="Continue"
        pending={pending}
        onSubmit={handleEnable}
      />
    );
  }

  if (step === "qr" && enrollData) {
    const secret = secretFromUri(enrollData.totpURI);
    return (
      <div className="space-y-5">
        <div>
          <p className="text-[14px] font-medium mb-1">
            Scan with your authenticator app
          </p>
          <p className="text-[13px] text-muted-foreground mb-4">
            Open Google Authenticator, Authy, 1Password, or any TOTP app and
            scan the code below.
          </p>
          <div className="flex flex-col items-start gap-4">
            <QrCanvas uri={enrollData.totpURI} />
            {secret && (
              <div>
                <p className="text-[12px] text-muted-foreground mb-1">
                  Can&apos;t scan? Enter this secret manually:
                </p>
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[13px] tracking-widest bg-muted/50 rounded px-3 py-1.5 border border-border/60">
                    {secret}
                  </span>
                  <CopyButton value={secret} />
                </div>
              </div>
            )}
          </div>
        </div>
        <div className="flex justify-end pt-2">
          <Button onClick={() => setStep("backup-codes")}>
            Next: save backup codes →
          </Button>
        </div>
      </div>
    );
  }

  if (step === "backup-codes" && enrollData) {
    return (
      <BackupCodesDisplay
        codes={enrollData.backupCodes}
        onConfirmed={() => setStep("verify")}
      />
    );
  }

  if (step === "verify") {
    return (
      <div className="space-y-4">
        <div>
          <p className="text-[14px] font-medium mb-1">
            Enter the 6-digit code from your authenticator
          </p>
          <p className="text-[13px] text-muted-foreground mb-3">
            This confirms your app is synced and arms the authenticator.
          </p>
          <div className="flex gap-2">
            <Input
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              placeholder="000 000"
              className="max-w-[140px] font-mono tracking-widest text-center text-[16px]"
              onKeyDown={(e) => {
                if (e.key === "Enter" && code.length === 6) handleVerify();
              }}
              autoFocus
            />
            <Button
              onClick={handleVerify}
              disabled={pending || code.length !== 6}
            >
              {pending ? "Verifying…" : "Verify & activate"}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return null;
}

// ─── RegenerateFlow ───────────────────────────────────────────────────────────

function RegenerateFlow({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<"password" | "codes">("password");
  const [pending, setPending] = useState(false);
  const [newCodes, setNewCodes] = useState<string[]>([]);

  const handleRegenerate = async (password: string) => {
    setPending(true);
    try {
      const result = await authClient.twoFactor.generateBackupCodes({ password });
      if (result.error) {
        toast.error(result.error.message ?? "Failed to regenerate backup codes");
        return;
      }
      // result.data has shape { status: boolean; backupCodes: string[] }
      const data = result.data as { status: boolean; backupCodes: string[] };
      setNewCodes(data.backupCodes ?? []);
      setStep("codes");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to regenerate backup codes");
    } finally {
      setPending(false);
    }
  };

  if (step === "password") {
    return (
      <PasswordPrompt
        label="Enter your current password to regenerate backup codes"
        buttonLabel="Regenerate"
        pending={pending}
        onSubmit={handleRegenerate}
      />
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-[14px] font-medium">Your new backup codes</p>
      <p className="text-[13px] text-muted-foreground">
        Your old codes are now invalid. Save these new ones somewhere safe.
      </p>
      <BackupCodesDisplay codes={newCodes} onConfirmed={onDone} />
    </div>
  );
}

// ─── SecuritySection (the exported section) ───────────────────────────────────

export function SecuritySection() {
  const { data: session, isPending: sessionLoading } = useSession();
  const [enrolling, setEnrolling] = useState(false);
  const [regenerating, setRegenerating] = useState(false);
  // Force a local re-render after enrollment — useSession() updates
  // asynchronously; we track it ourselves to flip the UI immediately.
  const [localEnrolled, setLocalEnrolled] = useState<boolean | null>(null);

  if (sessionLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  const user = session?.user as ({ twoFactorEnabled?: boolean } & Record<string, unknown>) | undefined;
  const isEnrolled = localEnrolled ?? (user?.twoFactorEnabled ?? false);

  const handleEnrollDone = () => {
    setLocalEnrolled(true);
    setEnrolling(false);
  };

  const handleRegenDone = () => {
    setRegenerating(false);
    toast.success("Backup codes replaced");
  };

  return (
    <div className="space-y-10">
      {/* Authenticator section */}
      <section>
        <SectionHeader title="Security" />

        <div className="divide-y divide-border/60">
          {/* Status row */}
          <div className="flex items-center justify-between py-3.5">
            <div className="flex items-start gap-3">
              <div
                className={cn(
                  "mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full",
                  isEnrolled
                    ? "bg-green-500/15 text-green-600 dark:text-green-400"
                    : "bg-muted text-muted-foreground",
                )}
              >
                {isEnrolled ? (
                  <ShieldCheck className="size-4" />
                ) : (
                  <Smartphone className="size-4" />
                )}
              </div>
              <div>
                <p className="text-[14px] font-medium">Reset authenticator</p>
                <p className="mt-0.5 text-[13px] text-muted-foreground">
                  {isEnrolled
                    ? "An authenticator app is enrolled. It will be used to verify your identity when you reset your password."
                    : "Link an authenticator app (Google Authenticator, Authy, 1Password, etc.) so you can securely reset your password without email access."}
                </p>
              </div>
            </div>
            {!enrolling && !regenerating && (
              <div className="ml-4 shrink-0">
                {isEnrolled ? (
                  <div className="flex items-center gap-2">
                    <span className="flex items-center gap-1.5 text-[12px] font-medium text-green-600 dark:text-green-400">
                      <ShieldCheck className="size-3.5" />
                      Enrolled
                    </span>
                  </div>
                ) : (
                  <Button size="sm" onClick={() => setEnrolling(true)}>
                    Set up
                  </Button>
                )}
              </div>
            )}
          </div>

          {/* Inline enrollment flow */}
          {enrolling && !isEnrolled && (
            <div className="py-5">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <KeyRound className="size-4 text-muted-foreground" />
                  <span className="text-[14px] font-medium">
                    Set up authenticator
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setEnrolling(false)}
                  className="text-[13px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  Cancel
                </button>
              </div>
              <EnrollFlow onDone={handleEnrollDone} />
            </div>
          )}

          {/* Backup codes row — only when enrolled */}
          {isEnrolled && (
            <div className="flex items-start justify-between py-3.5">
              <div className="flex items-start gap-3">
                <div className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                  <KeyRound className="size-4" />
                </div>
                <div>
                  <p className="text-[14px] font-medium">Backup codes</p>
                  <p className="mt-0.5 text-[13px] text-muted-foreground">
                    One-time codes you can use to authenticate if you lose
                    access to your authenticator app.
                  </p>
                </div>
              </div>
              {!regenerating && (
                <Button
                  variant="outline"
                  size="sm"
                  className="ml-4 shrink-0 gap-1.5"
                  onClick={() => setRegenerating(true)}
                >
                  <RefreshCw className="size-3.5" />
                  Regenerate
                </Button>
              )}
            </div>
          )}

          {/* Inline regenerate flow */}
          {regenerating && isEnrolled && (
            <div className="py-5">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <RefreshCw className="size-4 text-muted-foreground" />
                  <span className="text-[14px] font-medium">
                    Regenerate backup codes
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => setRegenerating(false)}
                  className="text-[13px] text-muted-foreground hover:text-foreground transition-colors"
                >
                  Cancel
                </button>
              </div>
              <RegenerateFlow onDone={handleRegenDone} />
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
