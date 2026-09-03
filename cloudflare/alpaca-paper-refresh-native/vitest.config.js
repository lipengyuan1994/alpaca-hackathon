import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [cloudflareTest({
    wrangler: { configPath: "./wrangler.jsonc" },
    miniflare: { bindings: { GITHUB_TOKEN: "test-only-token" } },
  })],
  test: { include: ["scheduler.spec.js"] },
});
