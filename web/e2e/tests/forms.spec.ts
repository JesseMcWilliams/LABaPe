// Request 11 (and the form parts of 06): the New environment form's live checks, and the token dialog.
// Nothing is created: the dialog is cancelled and the form is left unsubmitted.
import { test, expect } from "@playwright/test";
import { open, dialog, settle, WRITES } from "./helpers";

test("token dialog: a rate of 0 names 'Calls per minute'; destroyed environments are not offered", async ({ page }) => {
  await open(page, "/tokens");
  await page.getByRole("button", { name: "New token" }).click();
  const d = dialog(page);
  await d.getByLabel("console").check();
  await d.getByLabel("Only chosen environments").check();
  await d.getByRole("combobox", { name: /Environments/ }).click();
  const offered = await page.getByRole("option").allInnerTexts();
  for (const gone of ["accttest1", "autotest1", "fxcore1"]) expect(offered).not.toContain(gone);
  await page.keyboard.press("Escape");

  await d.getByLabel("Calls per minute").fill("0");
  await d.getByLabel("Name", { exact: true }).fill("zz-never-created");
  await d.getByRole("button", { name: "Create" }).click();           // refused: the rate is invalid, no token is made
  await expect(d).toContainText("Calls per minute");
  await expect(d).not.toContainText("Rate per minute");
  await d.getByRole("button", { name: "Cancel" }).click();
});

test("Settings → History mentions revoked or expired API tokens", async ({ page }) => {
  await open(page, "/admin/settings?section=stack");
  await expect(page.getByText(/revoked or expired API tokens/)).toBeVisible();
});

test.describe("New environment form", () => {
  test.beforeEach(async ({ page }) => {
    await open(page, "/environments/new");
  });

  test("a group name used twice gets the group-name message", async ({ page }) => {
    await page.getByLabel(/group name/i).first().fill("Web");
    await page.getByRole("button", { name: "Add host group" }).click();
    await page.getByLabel(/group name/i).nth(1).fill("web");
    await expect(page.getByText(/Also used by host group 1 \(names are compared without case\)/)).toBeVisible();
    await expect(page.getByText("The VM name web1 is used twice.")).toHaveCount(0);
    await page.getByLabel(/group name/i).nth(1).fill("db");
    await expect(page.getByText(/Also used by host group 1/)).toHaveCount(0);
  });

  test("Role follows the OS", async ({ page }) => {
    const os = page.getByRole("combobox").nth(2);   // OS: the first group's second field
    const role = page.getByRole("combobox").nth(3);   // Role
    const pick = async (box: typeof os, option: string) => {
      await box.click();
      await page.getByRole("option", { name: option, exact: true }).click();
    };
    await pick(os, "rocky9");
    await role.click();
    expect(await page.getByRole("option").allInnerTexts()).toEqual(["linux_server", "linux_workstation"]);
    await page.getByRole("option", { name: "linux_workstation" }).click();

    await pick(os, "windows_server_2022");
    await expect(role).toHaveText(/windows_server/);                  // changed by itself
    await role.click();
    expect((await page.getByRole("option").allInnerTexts()).sort())
      .toEqual(["domain_controller", "windows_server", "windows_workstation"]);
    await page.keyboard.press("Escape");

    await pick(os, "rocky9");
    await expect(role).toHaveText(/linux_/);
  });

  test("a chosen application is not offered twice", async ({ page }) => {
    const box = page.getByLabel("Applications").first();
    await box.click();
    const first = page.getByRole("option").first();
    const name = (await first.innerText()).trim();
    await first.click();
    await box.click();
    await expect(page.getByRole("option", { name, exact: true })).toHaveCount(0);
    await page.keyboard.press("Escape");
  });
});

test("an account whose hosts were all unticked is refused by name", async ({ page }) => {
  test.skip(!WRITES, "presses Save on lab2's accounts: run with npm run test:writes");
  await open(page, "/environments");
  await page.getByRole("link", { name: "lab2", exact: true }).first().click();
  await settle(page);
  await page.getByRole("heading", { name: "Accounts" }).waitFor();
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByRole("button", { name: "Add account" }).click();
  await page.getByPlaceholder("jdoe").last().fill("zzsudo");
  await page.getByRole("combobox", { name: /Domain/ }).last().click();
  await page.getByRole("option", { name: /local/i }).click();
  await page.getByRole("checkbox", { name: /linux_server/ }).last().check();
  const boxes = page.getByRole("checkbox");
  const withSudo = await boxes.count();
  await boxes.last().check();                                       // the new row's sudo box (it has no name of its own)
  await page.getByRole("checkbox", { name: /linux_server/ }).last().uncheck();
  await expect(boxes).toHaveCount(withSudo - 1);                   // the sudo box goes with the last Linux host
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(/Accounts: account \d+ \(zzsudo\)/)).toBeVisible();
  await page.reload();
  await expect(page.getByText("zzsudo")).toHaveCount(0);
});
