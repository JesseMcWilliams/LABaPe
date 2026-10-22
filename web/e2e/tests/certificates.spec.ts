// Request 16: the Certificates page. Looks only: nothing is requested, revoked, saved or added.
import { test, expect } from "@playwright/test";
import { open, dialog, settle } from "./helpers";

test.beforeEach(async ({ page }) => {
  await open(page, "/certificates");
});

test("the top bar has Certificates and the page has its three tabs", async ({ page }) => {
  await expect(page.locator("header").getByText("Certificates")).toBeVisible();
  for (const t of ["Certificates", "Request", "The CA"]) await expect(page.getByRole("tab", { name: t })).toBeVisible();
});

test("the list filters at once and clears", async ({ page }) => {
  await expect(page.getByLabel("Everyone's certificates")).toBeChecked();
  await expect(page.getByText(/\d+ certificates?/).first()).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Issued" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Expires" })).toBeVisible();

  const rows = page.getByRole("row").filter({ hasText: "zz-cert-test" });
  test.skip(await rows.count() === 0, "the zz-cert-test rows are not on this stack");

  await page.getByRole("combobox", { name: "State" }).click();
  await page.getByRole("option", { name: "revoked" }).click();           // applies without pressing Filter
  await settle(page);
  await expect(page.getByRole("row", { name: /\b(active|valid)\b/ })).toHaveCount(0);
  await page.getByRole("combobox", { name: "State" }).click();
  await page.getByRole("option", { name: "Any" }).click();

  await page.getByLabel("Search").fill("zz-cert");
  await page.getByRole("button", { name: "Filter" }).click();
  await settle(page);
  await expect(rows.first()).toBeVisible();
  await page.getByRole("button", { name: "Clear" }).click();
});

test("a revoked certificate opens read-only, with no private key", async ({ page }) => {
  const row = page.getByRole("row").filter({ hasText: "zz-cert-test" }).filter({ hasText: "revoked" }).first();
  test.skip(await row.count() === 0, "no revoked zz-cert-test row on this stack");
  await row.click();
  const d = dialog(page);
  await expect(d).toContainText(/Revoked by/);
  await expect(d).toContainText(/Serial \(hex\)/);
  await expect(d).toContainText(/SHA-256 fingerprint/);
  await expect(d).toContainText("-----BEGIN CERTIFICATE-----");
  await expect(d).not.toContainText("PRIVATE KEY");
  await expect(d.getByText("Download", { exact: true })).toBeVisible();
  await expect(d.getByRole("button", { name: /^Revoke/ })).toHaveCount(0);
  await d.getByRole("button", { name: "Close" }).click();
});

test("the Request tab: both forms, and the button stays disabled while empty", async ({ page }) => {
  await page.getByRole("tab", { name: "Request" }).click();
  await expect(page.getByText(/You may request names under/)).toBeVisible();
  const go = page.getByRole("button", { name: "Request the certificate" });
  await expect(page.getByRole("button", { name: "LABaPe makes the key" })).toHaveAttribute("aria-pressed", "true");
  for (const f of ["Common name", "More DNS names", "IP addresses", "Lifetime (days)", "For environment"]) {
    await expect(page.getByLabel(f).first()).toBeVisible();
  }
  await expect(go).toBeDisabled();                                   // never press it

  await page.getByRole("button", { name: "I have a CSR" }).click();
  await expect(page.getByLabel("Certificate request (PEM)")).toBeVisible();
  await expect(go).toBeDisabled();
  await page.getByRole("button", { name: "LABaPe makes the key" }).click();
});

test("The CA tab: status, root and the two admin boxes", async ({ page }) => {
  await page.getByRole("tab", { name: "The CA" }).click();
  await expect(page.getByText("answering")).toBeVisible();
  await expect(page.getByText("https://labape-ca:9000")).toBeVisible();
  await expect(page.getByText(/LABaPe CA Root CA, valid until/)).toBeVisible();
  await expect(page.getByText("In the register")).toBeVisible();
  await expect(page.getByRole("link", { name: "Download the root certificate" })).toBeVisible();

  await expect(page.getByText("What may be requested")).toBeVisible();
  await expect(page.getByLabel("Default lifetime (days)")).toHaveValue("30");
  await expect(page.getByLabel("Longest lifetime (days)")).toHaveValue("90");
  await expect(page.getByLabel("Allowed domains")).toBeVisible();
  await expect(page.getByLabel("Allowed networks")).toBeVisible();

  await expect(page.getByText("Add a certificate to the register")).toBeVisible();
  await expect(page.getByLabel("Certificate (PEM)")).toBeVisible();
  await expect(page.getByRole("button", { name: "Add", exact: true })).toBeDisabled();   // never press Save or Add
});
