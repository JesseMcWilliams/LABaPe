import { expect, Page } from "@playwright/test";

/** True only when the run may change the lab (npm run test:writes). */
export const WRITES = process.env.E2E_ALLOW_WRITES === "1";

/** Open a page and wait until it is signed in and the spinners are gone. */
export async function open(page: Page, path: string) {
  await page.goto(path);
  await expect(page).not.toHaveURL(/\/login/);        // a login page here means the saved session expired
  await settle(page);
}

export async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator(".MuiCircularProgress-root")).toHaveCount(0);   // spinners only: the load bars are progressbars too
}

/** The visible text of an MUI dialog. */
export const dialog = (page: Page) => page.getByRole("dialog");

/** Press the button of a "Are you sure?" confirmation, if one is shown. */
export async function confirm(page: Page, name: string | RegExp = /^(Confirm|OK|Yes|Save|Publish|Retire)/) {
  const d = dialog(page);
  await d.getByRole("button", { name }).last().click();
}

/** Open the first environment row named `name` from the Environments list. */
export async function openEnvironment(page: Page, name: string) {
  await open(page, "/environments");
  await page.getByRole("link", { name }).or(page.getByRole("cell", { name, exact: true })).first().click();
  await settle(page);
}
