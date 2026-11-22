import { defineConfig, devices } from "@playwright/test";

// End-to-end tests against a production build with fixture data (DATA_SOURCE=fixture): no
// Supabase, no secrets. Build first (`DATA_SOURCE=fixture npm run build`, or `make web-e2e`);
// Playwright then starts `next start` on port 3100 and runs the tests in Chromium only.
const PORT = 3100;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : 1, // one worker locally: a small machine
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `npx next start --port ${PORT}`,
    url: `http://localhost:${PORT}/about`,
    env: { DATA_SOURCE: "fixture" },
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
