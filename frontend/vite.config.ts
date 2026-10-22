import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

export default defineConfig({
  plugins: [
    react(),
    {
      name: "distribution-notices",
      apply: "build",
      generateBundle() {
        for (const fileName of ["LICENSE", "NOTICE"]) {
          this.emitFile({
            type: "asset",
            fileName,
            source: readFileSync(new URL(`../${fileName}`, import.meta.url)),
          });
        }
      },
    },
  ],
  server: {
    proxy: {
      "/api": {
        target: process.env.API_TARGET || "http://api:8000",
        changeOrigin: false,
      },
    },
  },
  build: {
    sourcemap: false,
    license: { fileName: "THIRD-PARTY-LICENSES.md" },
  },
});
