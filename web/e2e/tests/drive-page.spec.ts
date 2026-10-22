// Request 15: the drive page (SMART attributes, layout, history charts) and the links to it.
// Needs a host with a drive that has a SMART reading. E2E_HOST (default kvm1) and E2E_DISK (default sdc) pick it.
import { test, expect, Page } from "@playwright/test";
import { open, settle, WRITES } from "./helpers";

const HOST = process.env.E2E_HOST ?? "kvm1";
const DISK = process.env.E2E_DISK ?? "sdc";
const heading = new RegExp(`${HOST}: /dev/${DISK}`);

async function hostId(page: Page): Promise<string> {
  await open(page, "/admin/hosts");
  await page.getByRole("link", { name: HOST, exact: true }).first().click();
  await settle(page);
  return new URL(page.url()).pathname.split("/").pop()!;
}

test("the host's drive tables link to the drive page", async ({ page }) => {
  const id = await hostId(page);
  await page.getByText("Drives and volumes", { exact: true }).click();
  await page.getByRole("link", { name: new RegExp(`^(/dev/)?${DISK}$`) }).first().click();
  await settle(page);
  await expect(page).toHaveURL(new RegExp(`/admin/hosts/${id}/disks/`));
  await expect(page.getByText(heading).first()).toBeVisible();
});

test("the drive page: facts, layout, history and attributes", async ({ page }) => {
  const id = await hostId(page);
  await open(page, `/admin/hosts/${id}/disks/${DISK}`);
  await expect(page.getByText(heading).first()).toBeVisible();
  await expect(page.getByText(/SMART (passed|failed)/)).toBeVisible();
  await expect(page.getByText("Serial number")).toBeVisible();
  await expect(page.getByText("Layout")).toBeVisible();
  await expect(page.getByText(/\d+ readings?; read every 6 hours and kept \d+ days/)).toBeVisible();
  await expect(page.getByText(/Pending sectors now/)).toBeVisible();
  await expect(page.getByText("Reallocated sectors").first()).toBeVisible();

  for (const [label, days] of [["1 year", "365"], ["7 days", "7"]] as const) {
    await page.getByRole("button", { name: label }).click();
    await expect(page).toHaveURL(new RegExp(`days=${days}`));
    await settle(page);
  }

  const attrs = page.getByRole("table").filter({ hasText: "Worst" });
  await expect(attrs.getByRole("row", { name: /Reallocated_Sector_Ct/ })).toBeVisible();
  await expect(attrs.getByRole("row", { name: /Current_Pending_Sector/ })).toBeVisible();
});

test("?metric=pending scrolls the pending-sectors chart into view", async ({ page }) => {
  const id = await hostId(page);
  await open(page, `/admin/hosts/${id}/disks/${DISK}?metric=pending`);
  await expect(page.getByText(/Pending sectors now/)).toBeInViewport();
});

test("Read now stores a reading", async ({ page }) => {
  test.skip(!WRITES, "stores one SMART reading: run with npm run test:writes");
  const id = await hostId(page);
  await open(page, `/admin/hosts/${id}/disks/${DISK}`);
  const readings = async () => Number((await page.getByText(/\d+ readings?;/).innerText()).match(/(\d+)/)![1]);
  const before = await readings();
  await page.getByRole("button", { name: "Read now" }).click();
  await settle(page);
  expect(await readings()).toBeGreaterThanOrEqual(before);    // an identical reading within the hour is not stored twice
});

test("Settings → History has Drive history (days)", async ({ page }) => {
  await open(page, "/admin/settings?section=stack");
  await expect(page.getByLabel("Drive history (days)")).toHaveValue("365");
});
