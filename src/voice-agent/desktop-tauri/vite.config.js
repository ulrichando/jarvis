import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath, URL } from 'node:url'

const host = process.env.TAURI_DEV_HOST

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Explicit @ -> ./src alias (also declared in tsconfig paths) so the dev
  // server resolves it as reliably as the production build does.
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    host: host || false,
    hmr: host ? { protocol: 'ws', host, port: 5183 } : undefined,
  },
  envPrefix: ['VITE_', 'TAURI_ENV_*'],
  build: {
    chunkSizeWarningLimit: 1000,
    target: 'chrome105',
    minify: !process.env.TAURI_ENV_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules')) {
            const pkg = id.toString().split('node_modules/')[1].split('/')[0]
            if (['react', 'react-dom', 'react-is'].includes(pkg)) return 'vendor-react'
            if (['three', '@react-three', '@mediapipe'].includes(pkg)) return 'vendor-3d'
            if (['@livekit', 'livekit-client'].includes(pkg)) return 'vendor-livekit'
            if (['lucide-react', '@radix-ui', 'tailwindcss'].includes(pkg)) return 'vendor-ui'
            return 'vendor'
          }
        },
      },
    },
  },
})
