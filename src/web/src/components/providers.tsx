"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { useState } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";

// Voice-session listing + SSE transcript flow were removed 2026-05-22
// along with the rest of the hub subsystem. React Query is the cache
// layer for typed-chat fetches.

// One-time dev-only console filter for the noisy React 19 + react-markdown
// "A props object containing a key prop is being spread into JSX" warning.
// Our markdown components all use `stripKey()` before spreading, AND
// hast-util-to-jsx-runtime correctly passes key as the third arg to
// `_jsx(type, props, key)` — so the warning has no actionable cause in
// our code. It's a known React 19 dev-mode false-positive in this stack
// (react-markdown 10 + rehype-raw + React 19) that the upstream libs
// haven't patched. The match string is intentionally specific so genuine
// key-spread bugs anywhere else in the app still surface.
//
// Production: untouched. The original console.error path is unchanged
// outside `NODE_ENV === "development"`.
if (
  typeof window !== "undefined" &&
  process.env.NODE_ENV === "development" &&
  !(window as unknown as { __jarvisConsolePatched?: boolean })
    .__jarvisConsolePatched
) {
  (
    window as unknown as { __jarvisConsolePatched?: boolean }
  ).__jarvisConsolePatched = true;
  const original = console.error;
  console.error = (...args: unknown[]) => {
    const first = args[0];
    if (
      typeof first === "string" &&
      first.includes(
        'A props object containing a "key" prop is being spread into JSX',
      )
    ) {
      return;
    }
    original.apply(console, args as never);
  };
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delay={200}>
        {children}
        <Toaster richColors position="top-right" />
      </TooltipProvider>
      {process.env.NODE_ENV === "development" && (
        <ReactQueryDevtools initialIsOpen={false} />
      )}
    </QueryClientProvider>
  );
}
