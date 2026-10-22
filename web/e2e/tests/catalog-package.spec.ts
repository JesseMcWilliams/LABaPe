// Request 10: a published package refuses a draft item on save. Writes to the catalog (creates zz-pkg-<stamp>,
// retires it at the end), so it only runs with npm run test:writes. Catalog items cannot be deleted from the UI.
import { test, expect } from "@playwright/test";
import { open, dialog, settle, WRITES } from "./helpers";

test("a published package refuses a draft application", async ({ page }) => {
  test.skip(!WRITES, "creates a catalog item: run with npm run test:writes");
  const stamp = Date.now().toString(36);
  const pkg = `zz-pkg-${stamp}`;
  const confirmDialog = async () => { await dialog(page).getByRole("button", { name: /^(Confirm|OK|Yes|Save|Publish|Retire)/ }).last().click(); await settle(page); };

  // A draft application to include.
  await open(page, "/catalog");
  await page.getByRole("tab", { name: "Applications" }).click();
  await page.getByRole("link", { name: "New application" }).click();
  await page.getByLabel("Name", { exact: true }).fill(`zz-draft-${stamp}`);
  await page.getByLabel("Display name").fill(`ZZ draft ${stamp}`);
  await page.getByRole("button", { name: "Save as draft" }).click();
  await confirmDialog();

  await open(page, "/catalog");
  await page.getByRole("tab", { name: "Packages" }).click();
  await page.getByRole("link", { name: "New package" }).click();
  await page.getByLabel("Name", { exact: true }).fill(pkg);
  await page.getByLabel("Display name").fill(`ZZ package ${stamp}`);
  await page.getByLabel("Description").fill("test");
  const contents = page.getByLabel("Contents");
  await contents.click();
  await page.getByRole("option", { name: new RegExp(`ZZ draft ${stamp}`) }).click();
  await page.keyboard.press("Escape");
  await page.getByLabel("Name", { exact: true }).fill(pkg);                // the contents picker leaves the name empty
  await page.getByRole("button", { name: "Save as draft" }).click();
  await confirmDialog();

  await page.getByRole("button", { name: "Publish" }).click();
  await confirmDialog();
  await expect(page.getByText(new RegExp(`Publish application zz-draft-${stamp} first`))).toBeVisible();

  // The package stays a draft (Retire is offered only on published items); catalog items cannot be deleted here.
});
