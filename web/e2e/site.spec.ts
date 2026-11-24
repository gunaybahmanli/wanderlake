import { expect, test } from "@playwright/test";

// Fixture data (lib/data/fixtures.ts): Baku (AZN, Asia), Madrid (EUR, Europe), Tokyo (JPY, Asia).

test("home page loads with the monthly ranking", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Plan city trips with open data");
  await expect(page.getByRole("heading", { name: /^Good to visit in / })).toBeVisible();
  await expect(page.locator(".ranking-item")).toHaveCount(3);
  await expect(page.getByRole("navigation", { name: "Main" })).toBeVisible();
});

test("explore filters change the results and the URL", async ({ page }) => {
  const cards = page.locator(".card-grid > li");
  await page.goto("/explore");
  await expect(cards).toHaveCount(3);

  await page.getByLabel("Region").selectOption("Asia");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page).toHaveURL(/region=Asia/);
  await expect(cards).toHaveCount(2);
  await expect(cards.getByRole("heading")).toHaveText(["Baku", "Tokyo"]);

  await page.getByLabel("Travel month").selectOption("11");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect(page).toHaveURL(/month=11/);
  await expect(cards.getByRole("heading")).toHaveText(["Tokyo", "Baku"]); // best first

  await page.goto("/explore?currency=EUR");
  await expect(cards.getByRole("heading")).toHaveText(["Madrid"]);
});

test("city page converter: AZN default, amount and swap change the result", async ({ page }) => {
  await page.goto("/cities/tokyo");
  const converter = page.getByRole("region", { name: "Currency converter" });
  await expect(converter.getByLabel("From", { exact: true })).toHaveValue("AZN");
  await expect(converter.getByLabel("To", { exact: true })).toHaveValue("JPY");

  const result = page.getByTestId("converter-result");
  await expect(result).toContainText("AZN");
  await expect(result).toContainText("¥");
  const before = await result.innerText();

  await converter.getByLabel("Amount", { exact: true }).fill("250");
  await expect(result).not.toHaveText(before);
  await expect(result).toContainText("250.00");
  await expect(result).toContainText(/source: CBAR \(AZN\), ECB \(JPY\)/);

  await converter.getByRole("button", { name: /Swap currencies/ }).click();
  await expect(converter.getByLabel("From", { exact: true })).toHaveValue("JPY");

  await converter.getByLabel("Amount", { exact: true }).fill("abc");
  await expect(converter.getByText(/Use digits only/)).toBeVisible();
  await expect(converter.getByLabel("Amount", { exact: true })).toHaveAttribute("aria-invalid", "true");
});

test("Baku converts from AZN to USD by default", async ({ page }) => {
  await page.goto("/cities/baku");
  const converter = page.getByRole("region", { name: "Currency converter" });
  await expect(converter.getByLabel("To", { exact: true })).toHaveValue("USD");
  await expect(page.getByTestId("converter-result")).toContainText("US$58.82"); // 100 AZN at 1.7
});

test("climate chart renders with its data table", async ({ page }) => {
  await page.goto("/cities/madrid");
  const chart = page.getByTestId("climate-chart");
  await expect(chart).toBeVisible();
  await expect(chart.locator(".chart-month")).toHaveCount(12);
  await expect(chart.locator(".chart-best")).toHaveCount(3); // May, October, June
  await expect(chart.locator(".chart-rain")).toHaveCount(12);

  await page.getByText("Show the data as a table").click();
  const table = page.getByRole("table", { name: "Monthly climate in Madrid" });
  await expect(table.locator("tbody tr")).toHaveCount(12);
});

test("city photos show their credit; cities without one show the placeholder", async ({ page }) => {
  await page.goto("/cities/tokyo");
  const credit = page.locator(".city-cover-hero .photo-credit");
  await expect(credit).toContainText("Photo: Morio");
  await expect(credit.getByRole("link", { name: "CC BY-SA 3.0" })).toHaveAttribute(
    "href",
    "https://creativecommons.org/licenses/by-sa/3.0",
  );
  await expect(credit.getByRole("link", { name: "Wikimedia Commons" })).toHaveAttribute(
    "href",
    /commons\.wikimedia\.org\/wiki\/File:/,
  );
  await expect(page.getByRole("img", { name: "Photo of Tokyo" })).toBeAttached();

  await page.goto("/cities/baku");
  await expect(page.locator(".city-image-hero")).toBeVisible();
  await expect(page.locator(".photo-credit")).toHaveCount(0);

  await page.goto("/explore");
  await expect(page.locator(".city-card .photo-credit")).toHaveCount(1); // only Tokyo has a photo
});

test("the last publish time shows in the footer and on the About page", async ({ page }) => {
  // Fixture publishedAt (api.last_updated in production): 2026-11-22T17:48:00Z.
  await page.goto("/");
  await expect(page.locator(".site-footer")).toContainText("Data updated: 22 November 2026 at 17:48 UTC");
  await page.goto("/about");
  await expect(page.getByText("Data last updated:")).toContainText("22 November 2026 at 17:48 UTC");
});

test("an unknown city returns 404", async ({ page }) => {
  const response = await page.goto("/cities/atlantis");
  expect(response?.status()).toBe(404);
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});
