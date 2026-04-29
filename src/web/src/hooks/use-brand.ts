import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Brand } from "@/lib/design/brand";

const KEY = (id: string) => ["design-brand", id] as const;

export function useBrand(workspaceId: string) {
  return useQuery({
    queryKey: KEY(workspaceId),
    queryFn: async (): Promise<Brand | null> => {
      const r = await fetch(`/api/design/brand?workspaceId=${workspaceId}`);
      if (!r.ok) throw new Error(`brand ${r.status}`);
      const j = await r.json();
      return j.brand;
    },
  });
}

export function usePutBrand(workspaceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      brand: Brand;
      logoBase64?: string;
      logoFilename?: string;
    }) => {
      const r = await fetch(`/api/design/brand?workspaceId=${workspaceId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
      if (!r.ok) throw new Error(`brand put ${r.status}`);
      const j = await r.json();
      return j.brand as Brand;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY(workspaceId) });
    },
  });
}
