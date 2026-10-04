import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 45_000,
  use: {
    browserName: "chromium",
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
    headless: true,
    screenshot: "only-on-failure",
    trace: "off",
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  },
});
