import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for JARVIS UI E2E suite (Phase 7.4).
 *
 * Run with:
 *   npm run e2e        # headless
 *   npm run e2e:ui     # with the Playwright UI
 *
 * Tests live in `tests/e2e/`. Each test file targets one UI mode and
 * exercises the golden path. The themes test sweeps all 12 themes.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  fullyParallel: false,             // share localhost backend
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,                       // backend has shared state
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.JARVIS_UI_URL || "http://127.0.0.1:1420",
    actionTimeout: 5_000,
    navigationTimeout: 10_000,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: process.env.JARVIS_UI_URL ? undefined : {
    command: "npm run dev",
    url: "http://127.0.0.1:1420",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
