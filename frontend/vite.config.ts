import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://api:8000",
        changeOrigin: false,
      },
    },
  },
  build: { sourcemap: false },
});
