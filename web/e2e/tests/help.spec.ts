// Backlog row 24: the Help section, hosted by the stack, and the link from each page to its section.
import { test, expect } from "@playwright/test";
import { open } from "./helpers";

test("Help opens the guide at the section asked for", async ({ page }) => {
  await open(page, "/help?doc=guide&find=Creating%20an%20environment");
  await expect(page.getByRole("heading", { name: /Creating an environment/ })).toBeVisible();
  await expect(page.getByRole("tab", { name: /Using the web interface/ })).toBeVisible();
});

test("a page links to its Help section", async ({ page }) => {
  await open(page, "/jobs");
  await page.getByRole("link", { name: "Help for this page" }).click();
  await expect(page).toHaveURL(/\/help\?doc=guide&find=Jobs/);
  await expect(page.getByRole("heading", { name: /Jobs/ }).first()).toBeVisible();
});

test("Files has a Linked stacks tab", async ({ page }) => {
  await open(page, "/files?tab=linked");
  await expect(page.getByRole("tab", { name: "Linked stacks" })).toBeVisible();     // listing only: never press Copy here
});
