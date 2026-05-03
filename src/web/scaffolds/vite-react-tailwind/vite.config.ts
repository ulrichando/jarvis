import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Polling watch is required for Vite dev to see file changes inside a
// Docker bind mount.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    watch: { usePolling: true, interval: 300 },
  },
});
