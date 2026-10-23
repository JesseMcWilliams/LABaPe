// Environment templates (design §68): the starters are listed and starting from one fills the New environment form.
// Reads only: nothing is created.
import { test, expect } from "@playwright/test";
import { open } from "./helpers";

test("the Templates page lists the starters", async ({ page }) => {
  await open(page, "/environment-templates");
  for (const name of ["Domain with members", "Three-tier Linux (standalone)", "Kerberos lab"]) {
    await expect(page.getByRole("row").filter({ hasText: name }).first()).toBeVisible();
  }
  await expect(page.getByRole("button", { name: "New from parts" })).toBeVisible();
});

test("starting from a starter fills the New environment form", async ({ page }) => {
  await open(page, "/environments/new");
  await page.getByText("Start from a template").click();
  await page.locator(".MuiAccordion-root").getByRole("combobox").click();
  await page.getByRole("option", { name: /Kerberos lab/ }).click();
  await page.getByRole("button", { name: "Use this template" }).click();
  await expect(page.getByLabel("Group name").first()).toHaveValue("dc");
  await expect(page.getByLabel("Group name")).toHaveCount(3);
});
