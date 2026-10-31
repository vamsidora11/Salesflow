import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In dev, the API runs on the host at :8000; in Compose, nginx does this proxying.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", ws: true },
      "/health": "http://localhost:8000",
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
