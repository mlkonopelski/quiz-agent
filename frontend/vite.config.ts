import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/ui/" : "/",
  plugins: [react()],
  server: {
    proxy: {
      "/auth": "http://127.0.0.1:8000",
      "/sessions": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
  },
}));
