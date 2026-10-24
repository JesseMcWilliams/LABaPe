// The 2026-10-23 review's pages (questions 196-202): Admin → Users, the Sharing panel, and browser SSH to a VM
// whose SSH host key LABaPe has recorded (the guacd host-key check, question 198).
// Needs a deployed Linux environment: E2E_SHARE_ENV (its id) and E2E_SSH_VM (its VM name); tests skip without them.
import { test, expect } from "@playwright/test";
import { open, dialog, settle, WRITES } from "./helpers";

const ENV_ID = process.env.E2E_SHARE_ENV ?? "";
const VM = process.env.E2E_SSH_VM ?? "";

test("Admin → Users lists accounts with Disable (nothing is pressed)", async ({ page }) => {
  await open(page, "/admin/users");
  await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
  await expect(page.getByRole("cell", { name: /labape-admin/ }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Disable" }).first()).toBeVisible();
  await page.getByLabel("Search").fill("viewer");
  await settle(page);
  await expect(page.getByRole("cell", { name: /labape-viewer/ })).toBeVisible();
  await expect(page.getByRole("cell", { name: /^labape-admin/ })).toHaveCount(0);
});

test("the Admin menu has Users and Help knows it", async ({ page }) => {
  await open(page, "/admin/users");
  await expect(page.getByRole("link", { name: "Users" }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /Help/ }).first()).toBeVisible();
});

test.describe("Sharing panel", () => {
  test.skip(!ENV_ID, "set E2E_SHARE_ENV to an environment's id");

  test("shows the owner and the Hand over button", async ({ page }) => {
    await open(page, `/environments/${ENV_ID}`);
    await expect(page.getByRole("heading", { name: "Sharing" })).toBeVisible();
    await expect(page.getByText(/· owner/)).toBeVisible();
    await page.getByRole("button", { name: "Hand over" }).click();
    await expect(dialog(page)).toContainText("Hand this environment over");
    await dialog(page).getByRole("button", { name: "Cancel" }).click();
  });

  test("share with a user and take it back (writes only)", async ({ page }) => {
    test.skip(!WRITES, "changes who can see the environment");
    await open(page, `/environments/${ENV_ID}`);
    await page.getByLabel("Username").fill("labape-viewer");
    await page.getByRole("button", { name: "Share", exact: true }).click();
    const chip = page.locator(".MuiChip-root", { hasText: "labape-viewer" });
    await expect(chip).toBeVisible();
    await chip.locator(".MuiChip-deleteIcon").click();
    await expect(chip).toHaveCount(0);
  });
});

test("browser SSH to a VM with a recorded host key connects (question 198)", async ({ page }) => {
  test.skip(!ENV_ID || !VM, "set E2E_SHARE_ENV and E2E_SSH_VM");
  test.skip(!WRITES, "opens a (recorded) remote session");
  await open(page, `/environments/${ENV_ID}/vms/${VM}`);
  await page.getByRole("button", { name: "Console", exact: true }).click();
  await page.getByRole("button", { name: "SSH", exact: true }).click();
  await expect(page).toHaveURL(/\/remote\//);
  await expect(page.locator(".MuiChip-root", { hasText: "connected" })).toBeVisible({ timeout: 40_000 });
  await expect(page.getByRole("alert")).toHaveCount(0);
});
