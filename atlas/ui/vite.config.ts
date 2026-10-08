import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built to static assets and served by FastAPI on the mesh interface. The
// dashboard is never added to the public Caddy listener, so there is no CDN,
// no analytics and no external origin in the bundle.
export default defineConfig({
  plugins: [react()],
  // The wake model's worker (src/voice/whisperWorker.ts) loads
  // transformers.js with a dynamic import, so it has to be an ES-module
  // worker: the default "iife" format cannot split chunks.
  worker: {
    format: "es",
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    // One person on a mesh link. Readable stack traces beat a few saved KB.
    sourcemap: true,
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/brief": "http://127.0.0.1:8080",
      "/f": "http://127.0.0.1:8080",
    },
  },
});
