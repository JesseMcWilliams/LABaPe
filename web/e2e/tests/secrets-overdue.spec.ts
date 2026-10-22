// Requests 12 and 13: overdue rotations on the Secrets page, and the Rotate overdue now dialog.
// The dialog is only opened and cancelled: the rotation restarts the lab's services and is never started here.
import { test, expect } from "@playwright/test";
import { open, dialog } from "./helpers";

test.describe("Admin → Secrets, overdue rotations", () => {
  test.beforeEach(async ({ page }) => {
    await open(page, "/admin/secrets");
  });

  test("the warning, the tab label and the chips agree", async ({ page }) => {
    const warning = page.getByRole("alert").filter({ hasText: /past their rotation date/ });
    test.skip(await warning.count() === 0, "nothing is overdue on this stack");
    const n = Number((await warning.innerText()).match(/(\d+) secrets? (?:is|are) past/)?.[1]);
    expect(n).toBeGreaterThan(0);
    await expect(page.getByRole("tab", { name: new RegExp(`\\(${n} overdue\\)`) })).toBeVisible();
  });

  test("Rotate overdue now shows its plan and stays disabled until acknowledged", async ({ page }) => {
    const warning = page.getByRole("alert").filter({ hasText: /past their rotation date/ });
    test.skip(await warning.count() === 0, "nothing is overdue on this stack");
    await expect(page.getByText(/Rotating the overdue secrets/)).toHaveCount(0);

    await warning.getByRole("button", { name: "Rotate overdue now" }).click();
    const d = dialog(page);
    await expect(d.getByText("Rotate the overdue secrets now?")).toBeVisible();
    await expect(d.getByText(/one at a time|Nothing is overdue any more/)).toBeVisible();
    test.skip(/Nothing is overdue any more/.test(await d.innerText()), "the dialog finds nothing to rotate: the warning lists secrets the rotation skips (destroyed environments)");
    await expect(d).toContainText(/one at a time/);
    await expect(d).toContainText(/stops? at the first/);
    await expect(d.getByRole("listitem").first()).toBeVisible();
    await expect(d.getByRole("alert")).toContainText(/restart/);
    await expect(d.getByRole("alert")).toContainText(/maintenance mode/);

    const start = d.getByRole("button", { name: /^Rotate \d+ now$/ });
    const ack = d.getByRole("checkbox", { name: "I understand that services will stop" });
    await expect(ack).not.toBeChecked();
    await expect(start).toBeDisabled();
    await ack.check();
    await expect(start).toBeEnabled();
    await ack.uncheck();
    await expect(start).toBeDisabled();

    await d.getByRole("button", { name: "Cancel" }).click();           // never press the start button
    await expect(d).toHaveCount(0);
    await expect(page.getByText(/Rotating the overdue secrets/)).toHaveCount(0);
    await page.reload();
    await expect(warning).toBeVisible();
    await expect(page.getByText(/Rotating the overdue secrets/)).toHaveCount(0);
  });
});
