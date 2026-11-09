/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the viewer proxies /api to the FastAPI server.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.AGENTPROOF_API ?? "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
});
