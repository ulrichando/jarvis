"use client";

import { use } from "react";
import { ArtifactDetail } from "@/components/artifacts/artifact-detail";

export default function ArtifactDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return <ArtifactDetail id={id} />;
}
