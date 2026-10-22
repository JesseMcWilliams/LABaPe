// Saves a signed-in session for the other tests. Two ways:
//   1. LABAPE_SESSION_COOKIE=<value of the labape_session cookie> (copied from a signed-in browser;
//      the only way inside the Playwright container, which has no display), or
//   2. `npm run login` on a machine with a display: sign in by hand in the window that opens.
// The state file holds a live session: it stays in the gitignored state/ folder.
import { test as setup } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { STATE_FILE } from "../playwright.config";

setup("save a signed-in session", async ({ page, baseURL, context }) => {
  mkdirSync("state", { recursive: true });
  const cookie = process.env.LABAPE_SESSION_COOKIE;
  if (cookie) {
    const url = new URL(baseURL!);
    writeFileSync(STATE_FILE, JSON.stringify({
      cookies: [{ name: "labape_session", value: cookie, domain: url.hostname, path: "/",
        httpOnly: true, secure: url.protocol === "https:", sameSite: "Lax", expires: -1 }],
      origins: [],
    }));
    return;
  }
  await page.goto("/");
  console.log("Sign in in the browser window (you have 5 minutes) ...");
  await page.getByRole("link", { name: "Environments" }).or(page.getByRole("button", { name: "Environments" }))
    .first().waitFor({ timeout: 290_000 });
  await context.storageState({ path: STATE_FILE });
});
