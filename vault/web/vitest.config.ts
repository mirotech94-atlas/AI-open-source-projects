import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Web-track verification runs entirely offline: jsdom + fixtures, never a live
// hub (plan: "Parallel-track verification discipline"). `globals: false` keeps
// `describe`/`it`/`expect` explicit imports, so `tests/setup.ts` has to arm
// Testing Library's cleanup itself.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.{ts,tsx}"],
    css: false,
    // Above `asyncUtilTimeout` (tests/setup.ts), and deliberately so: a query
    // allowed five seconds to find its element inside a test allowed five
    // seconds means the test's whole budget can be spent waiting, and the
    // failure arrives as a timeout rather than as the assertion. That is
    // exactly what happened when the query timeout was first raised on its
    // own -- nine a11y checks, each failing at 5005ms, each of them a test
    // that had passed a minute earlier.
    //
    // The a11y file needs the headroom on its own merits anyway: it runs axe
    // over nine full pages in two skins and takes the better part of a minute.
    testTimeout: 20000,
  },
});
