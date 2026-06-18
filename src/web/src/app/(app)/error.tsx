"use client";

// App-segment error boundary (Next.js): catches render/runtime errors anywhere
// under (app) — /code, /chat, /projects, etc. — and shows a recoverable
// fallback instead of a blank screen. `reset()` re-renders the segment.
import { useEffect } from "react";

export default function AppError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surfaced in the server/browser console for debugging.
    console.error("[app] render error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="text-[15px] font-semibold text-foreground">Something went wrong</div>
      <p className="max-w-md text-[13px] text-muted-foreground">
        This part of Jarvis hit an unexpected error. Your work is saved — try again, or reload.
        {error?.digest && (
          <span className="mt-1 block font-mono text-[11px] text-muted-foreground/60">
            ref: {error.digest}
          </span>
        )}
      </p>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={reset}
          className="rounded-md bg-orange-600 px-3 py-1.5 text-[13px] font-medium text-white hover:bg-orange-500"
        >
          Try again
        </button>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-md border border-border px-3 py-1.5 text-[13px] text-foreground hover:bg-accent/50"
        >
          Reload
        </button>
      </div>
    </div>
  );
}
