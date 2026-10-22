// Browser tests for the web interface, run against a live stack (README.md in this folder).
import { defineConfig } from "@playwright/test";

const baseURL = process.env.LABAPE_URL ?? "";
if (!baseURL && !process.env.PW_LIST_ONLY) {
  console.warn("LABAPE_URL is not set (for example https://oz.example): the tests will fail.");
}

export const STATE_FILE = "state/admin.json";

export default defineConfig({
  testDir: "tests",
  outputDir: "results",
  reporter: [["list"], ["html", { outputFolder: "report", open: "never" }]],
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL,
    ignoreHTTPSErrors: process.env.LABAPE_INSECURE_TLS === "1",   // the lab's platform CA is not in the browser's store
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    // `npm run login`: opens a visible browser, you sign in, the session is saved to state/admin.json.
    { name: "login", testMatch: /login\.setup\.ts/, timeout: 300_000 },
    { name: "read-only", testMatch: /\.spec\.ts/, use: { storageState: STATE_FILE } },
  ],
});
