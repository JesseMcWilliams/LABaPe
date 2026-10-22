// Request 14: the Add host dialog on a deployed environment. Cancelled every time: no deploy is started.
// E2E_ENV (default lab2) names the environment to open.
import { test, expect, Page } from "@playwright/test";
import { open, dialog, settle } from "./helpers";

const ENV = process.env.E2E_ENV ?? "lab2";

async function openDialog(page: Page) {
  await open(page, "/environments");
  await page.getByRole("link", { name: ENV, exact: true }).first().click();
  await settle(page);
  await page.getByRole("button", { name: /^Redeploy/ }).first().waitFor({ timeout: 10_000 }).catch(() => undefined);   // the page fills in after the first load
  const add = page.getByRole("button", { name: "Add host", exact: true });
  test.skip(await add.count() === 0, `${ENV} is not deployed (no Add host button)`);
  await add.click();
  return dialog(page);
}

test("Add host sits between Redeploy and Preview redeploy", async ({ page }) => {
  await openDialog(page);
  await dialog(page).getByRole("button", { name: "Cancel" }).click();
  await expect(dialog(page)).toHaveCount(0);
  const names = (await page.getByRole("button").allInnerTexts()).map((t) => t.trim());
  const i = names.findIndex((t) => /^add host$/i.test(t));
  expect(names.slice(0, i).some((t) => /^Redeploy/i.test(t))).toBe(true);
  expect(names.slice(i).some((t) => /Preview redeploy/i.test(t))).toBe(true);
});

test("more hosts in a group: names and count limits", async ({ page }) => {
  const d = await openDialog(page);
  await expect(d.getByText(`Add hosts to ${ENV}`)).toBeVisible();
  await expect(d.getByRole("button", { name: "More hosts in a group" })).toHaveAttribute("aria-pressed", "true");
  await expect(d.getByRole("button", { name: "A new group like an existing one" })).toBeVisible();
  const count = d.getByLabel("How many");
  await expect(count).toHaveValue("1");
  await expect(d.getByLabel(/^New host 1/)).toBeVisible();

  await count.fill("3");
  await expect(d.getByLabel(/^New host \d/)).toHaveCount(3);

  const go = d.getByRole("button", { name: /^Add .*hosts? and deploy$/ });
  {   // the box clamps to 1-20, and the group already has hosts, so 20 is too many
    await count.fill("20");
    await expect(go).toBeDisabled();
  }
  await count.fill("1");
  await expect(go).toBeEnabled();
  await d.getByRole("button", { name: "Cancel" }).click();
});

test("a new group like an existing one: the name is checked at once", async ({ page }) => {
  const d = await openDialog(page);
  await d.getByRole("button", { name: "A new group like an existing one" }).click();
  const name = d.getByLabel("New group name");
  const go = d.getByRole("button", { name: /^Add .*hosts? and deploy$/ });
  await expect(d.getByLabel("Copy of group")).toBeVisible();
  await expect(go).toBeDisabled();

  await name.fill("1bad");
  await expect(d.getByText(/Use 1-12 letters and digits, starting with a letter/)).toBeVisible();
  await expect(go).toBeDisabled();

  // The copied group's own name in capitals: already used, names are compared without case.
  const first = (await d.getByLabel("Copy of group").innerText()).trim().split(/[\s(]/)[0];
  await name.fill(first.toUpperCase());
  await expect(d.getByText(/already used/i)).toBeVisible();

  await name.fill("Cache");
  await expect(go).toBeEnabled();
  await d.getByRole("button", { name: "Cancel" }).click();           // never press the deploy button
  await expect(dialog(page)).toHaveCount(0);
});
