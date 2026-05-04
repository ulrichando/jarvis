// Shared shape of the /api/workspace/[id]/verify response. Defined
// here (instead of inline in chat.tsx) so client components like
// VerifyPill / Thread / Message can type-check against the same
// contract without importing the server-only verify route.
export type VerifyOutcome = {
  ok: boolean;
  fixers: Array<{
    rule: string;
    filesChanged: string[];
    description: string;
  }>;
  typecheck: { ran: boolean; ok: boolean; output: string };
  preview: { ran: boolean; ok: boolean; status: number | null };
  screenshot?: { dataUrl: string; bytes: number; target: string };
  durationMs: number;
};
