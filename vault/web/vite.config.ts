import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// https://vite.dev/config/
export default defineConfig({
  // The commit this bundle is built from, so a running page can tell whether
  // the server has since deployed a newer one -- see `lib/buildwatch`.
  define: { __BUILD_SHA__: JSON.stringify(process.env.GUARDIAN_BUILD_SHA ?? "dev") },
  plugins: [react()],
  base: "/",
  server: {
    proxy: {
      // ws: the live feed is `/api/v1/ws`; without it the dev server never
      // upgrades the socket and every "Live" indicator reads as disconnected.
      "/api": { target: "http://127.0.0.1:8800", ws: true },
    },
  },
  build: {
    outDir: "dist",
    // Vite's default "assets" output dir collides with the app's own
    // `/assets` SPA route (Websites/Servers/Devices/Screen-time) once the
    // hub mounts it as a static prefix in main.py's `_mount_web` — a
    // request for `/assets/devices` would resolve inside the StaticFiles
    // mount instead of falling through to the SPA catch-all, 404ing every
    // Assets sub-route. Keep this in sync with `hub/guardian_hub/main.py`.
    assetsDir: "static",
  },
});
