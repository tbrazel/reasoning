import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins: [react(), {
    name: "reasoning-license-material",
    generateBundle() {
      for (const [source, target] of [["LICENSE", "licenses/Reasoning-Apache-2.0.txt"], ["NOTICE", "licenses/Reasoning-NOTICE.txt"]]) {
        this.emitFile({ type: "asset", fileName: target, source: readFileSync(new URL(`../${source}`, import.meta.url), "utf8") });
      }
    },
  }],
  build: {
    manifest: true,
    rollupOptions: {
      input: {
        workbench: fileURLToPath(new URL("index.html", import.meta.url)),
        organization: fileURLToPath(new URL("organization.html", import.meta.url)),
      },
    },
  },
  server: {
    proxy: {
      "/api": `http://127.0.0.1:${process.env.REASONING_DEV_BACKEND_PORT || "9119"}`,
    },
  },
});
