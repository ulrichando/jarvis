"use client";

// Evolution QUEUE depth — drives the badge on the Evolution nav item so you see
// how many self-improvement intents are queued to build, without opening the
// page. Was data.proposals.length (built proposals awaiting review), which never
// matched the queue the user actually wanted to see. Source of truth is
// GET /api/evolution's status.queued (= queued.length). With the 4h build
// cadence the depth moves through the day, so poll a bit faster than the old
// overnight cadence.
import { useQuery } from "@tanstack/react-query";

type EvolutionResp = { status?: { queued?: number } };

export function useEvolutionCount(): number {
  const { data } = useQuery({
    queryKey: ["evolution-count"],
    queryFn: async (): Promise<EvolutionResp> => {
      const res = await fetch("/api/evolution", { cache: "no-store" });
      if (!res.ok) return {};
      return (await res.json()) as EvolutionResp;
    },
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });
  return data?.status?.queued ?? 0;
}
