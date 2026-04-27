"use client";

import { motion } from "motion/react";
import type { Provider } from "@/lib/ai/models-meta";
import { getProviderUX } from "@/lib/ai/provider-ux";

export function EmptyState({
  name,
  provider,
}: {
  name?: string;
  provider: Provider;
}) {
  const ux = getProviderUX("anthropic");

  return (
    <motion.div
      key="greeting"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      {ux.renderGreeting({ name, hour: new Date().getHours() })}
    </motion.div>
  );
}
