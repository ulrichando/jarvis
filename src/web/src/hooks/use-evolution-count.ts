"use client";

// Pending self-evolution proposals count — drives the badge on the Evolution
// nav item so you know JARVIS has something waiting for your review without
// opening the page. Proposals land overnight, so a slow poll + refetch-on-focus
// is plenty; the page itself (GET /api/evolution) is the source of truth.
import { useQuery } from "@tanstack/react-query";

type EvolutionList = { proposals?: unknown[] };

export function useEvolutionCount(): number {
  const { data } = useQuery({
    queryKey: ["evolution-count"],
    queryFn: async (): Promise<EvolutionList> => {
      const res = await fetch("/api/evolution", { cache: "no-store" });
      if (!res.ok) return { proposals: [] };
      return (await res.json()) as EvolutionList;
    },
    refetchInterval: 5 * 60_000, // proposals only appear overnight
    refetchOnWindowFocus: true,
    staleTime: 60_000,
  });
  return data?.proposals?.length ?? 0;
}
