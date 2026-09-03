import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The UI never talks to MT5 or the broker. Everything goes through the core's local API (D7),
// proxied here so dev and the packaged Electron app use the same relative paths.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.CORE_URL || "http://127.0.0.1:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
      // The event stream gets its own prefix rather than living under /api. Sharing a prefix that
      // also serves HTTP made Vite confuse the upgrade with its own HMR socket, and the UI sat
      // there saying "disconnected" while the core was perfectly healthy.
      "/ws": {
        target: process.env.CORE_URL || "http://127.0.0.1:8001",
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
  // Vitest reads this file too. The UI has no backend of its own to test against: what is worth
  // testing here is the arithmetic the header does on the core's numbers, and what the client
  // concludes when the core does not answer. Both have already been wrong once.
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{js,jsx}"],
    restoreMocks: true,
  },
});
