import { defineConfig, devices } from "@playwright/test";
if (process.env.E2E_ALLOW_MUTATIONS !== "1")
  throw new Error(
    "Use an isolated test deployment and set E2E_ALLOW_MUTATIONS=1",
  );
if (!process.env.E2E_BASE_URL)
  throw new Error("Set E2E_BASE_URL to the isolated test deployment");
export default defineConfig({
  testDir: "./e2e",
  timeout: 60000,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1000 },
      },
    },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
