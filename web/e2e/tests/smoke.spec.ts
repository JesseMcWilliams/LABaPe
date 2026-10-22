// Every main page opens signed in and raises no script error or failed API call.
import { test, expect } from "@playwright/test";
import { open } from "./helpers";

const pages: [string, string][] = [
  ["Dashboard", "/"], ["Environments", "/environments"], ["New environment", "/environments/new"],
  ["Jobs", "/jobs"], ["Files", "/files"], ["Catalog", "/catalog"], ["Certificates", "/certificates"],
  ["Syslog", "/syslog"], ["API tokens", "/tokens"], ["KVM hosts", "/admin/hosts"],
  ["Networks", "/admin/networks"], ["Settings", "/admin/settings"], ["Mail", "/admin/mail"],
  ["Secrets", "/admin/secrets"], ["Audit", "/admin/audit"], ["Errors", "/admin/errors"],
  ["Help", "/help"], ["Sessions", "/admin/sessions"], ["Links", "/admin/links"], ["Quotas", "/admin/quotas"],
];

for (const [name, path] of pages) {
  test(`${name} (${path}) opens without errors`, async ({ page }) => {
    const problems: string[] = [];
    page.on("pageerror", (e) => problems.push(`script error: ${e.message}`));
    page.on("response", (r) => {
      if (r.url().includes("/api/") && r.status() >= 500) problems.push(`${r.status()} ${r.url()}`);
    });
    await open(page, path);
    await expect(page.getByText("Not found.")).toHaveCount(0);
    expect(problems).toEqual([]);
  });
}

test("the top bar stays visible while a long page scrolls", async ({ page }) => {
  await open(page, "/admin/secrets");                                  // a long page
  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(200);
  const bar = page.locator("header").first();
  await expect(bar).toBeInViewport();
  expect((await bar.boundingBox())!.y).toBeLessThanOrEqual(1);
});
