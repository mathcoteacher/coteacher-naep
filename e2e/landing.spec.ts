import { test, expect } from "@playwright/test";

// Block external API calls so tests are deterministic (IP geo, BigDataCloud)
test.beforeEach(async ({ page }) => {
  // Block IP geolocation APIs so they don't affect state
  await page.route("**/ipapi.co/**", (route) => route.abort());
  await page.route("**/ipwho.is/**", (route) => route.abort());
});

// ============================================================
// Test Group A: Page Structure & CTAs
// ============================================================

test.describe("Page Structure & CTAs", () => {
  test('primary CTA "Math CoTeacher is a solution" exists inside .story-side', async ({
    page,
  }) => {
    await page.goto("/");
    const cta = page.locator(".story-side .cta-button");
    await expect(cta).toBeVisible();
    await expect(cta).toContainText("Math CoTeacher");
  });

  test('"Explore the data" exists inside .map-side with .explore-link class', async ({
    page,
  }) => {
    await page.goto("/");
    const explore = page.locator(".map-side .explore-link");
    await expect(explore).toBeVisible();
    await expect(explore).toContainText("Explore");
  });

  test('"Explore the data" does NOT have .cta-button class (secondary styling)', async ({
    page,
  }) => {
    await page.goto("/");
    const explore = page.locator(".map-side .explore-link");
    // It should NOT also have the cta-button class
    await expect(explore).not.toHaveClass(/cta-button/);
  });
});

// ============================================================
// Test Group B: Explore Link Navigation
// ============================================================

test.describe("Explore Link Navigation", () => {
  test("clicking explore link from / does not 404", async ({ page }) => {
    await page.goto("/");
    // Wait for the page to be ready
    await page.waitForLoadState("networkidle");

    const explore = page.locator(".explore-link");
    await explore.click();

    await page.waitForLoadState("networkidle");
    await expect(page).toHaveTitle(/Explore/);
    // Should NOT show a 404
    await expect(page.locator("body")).not.toContainText("Page not found");
  });

  test("explore link includes state param after selectState", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Trigger state selection programmatically
    await page.evaluate(() => {
      (window as any).selectState("OH");
    });

    const href = await page.locator(".explore-link").getAttribute("href");
    expect(href).toContain("state=OH");
  });
});

// ============================================================
// Test Group C: Geolocation Label State Transitions
// ============================================================

test.describe("Geolocation Label State Transitions", () => {
  test("IP geolocation sets headline to state name only", async ({ page }) => {
    // Mock IP geolocation to return Ohio
    await page.route("**/ipapi.co/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          latitude: 39.96,
          longitude: -83.0,
          city: "Columbus",
          region_code: "OH",
        }),
      })
    );

    await page.goto("/");
    // Wait for the animation + text to appear
    await page.waitForFunction(
      () => document.getElementById("stateLabel")?.textContent === "Ohio",
      null,
      { timeout: 10000 }
    );

    const label = await page.locator("#stateLabel").textContent();
    expect(label).toBe("Ohio");
  });

  test("GPS geolocation updates headline to city + state", async ({
    page,
  }) => {
    // Block IP geo
    await page.route("**/ipapi.co/**", (route) => route.abort());

    // Mock BigDataCloud reverse geocoding (Canal Winchester fixture)
    await page.route("**/api.bigdatacloud.net/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          city: "",
          locality: "Township of Madison",
          principalSubdivision: "Ohio",
          principalSubdivisionCode: "US-OH",
          localityInfo: {
            informative: [
              {
                name: "Canal Winchester",
                order: 5,
                description: "populated place",
              },
            ],
          },
        }),
      })
    );

    // Mock navigator.geolocation
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "geolocation", {
        value: {
          getCurrentPosition: (success: PositionCallback) => {
            success({
              coords: {
                latitude: 39.84,
                longitude: -82.8,
                accuracy: 10,
                altitude: null,
                altitudeAccuracy: null,
                heading: null,
                speed: null,
              },
              timestamp: Date.now(),
            } as GeolocationPosition);
          },
        },
      });
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // Click "Use my location"
    const locBtn = page.locator("#locBtn");
    await locBtn.click();

    // Wait for the button text to update
    await page.waitForFunction(
      () => document.getElementById("locBtn")?.textContent?.includes("Canal Winchester"),
      null,
      { timeout: 10000 }
    );

    // Verify button text
    const btnText = await locBtn.textContent();
    expect(btnText).toBe("My location: Canal Winchester, Ohio");

    // Verify headline uses city + state
    const label = await page.locator("#stateLabel").textContent();
    expect(label).toBe("Canal Winchester, Ohio");
  });

  test("manual state click after GPS resets to state-only label", async ({
    page,
  }) => {
    // Mock BigDataCloud
    await page.route("**/api.bigdatacloud.net/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          city: "",
          locality: "Township of Madison",
          principalSubdivision: "Ohio",
          principalSubdivisionCode: "US-OH",
          localityInfo: {
            informative: [
              {
                name: "Canal Winchester",
                order: 5,
                description: "populated place",
              },
            ],
          },
        }),
      })
    );

    // Mock navigator.geolocation
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "geolocation", {
        value: {
          getCurrentPosition: (success: PositionCallback) => {
            success({
              coords: {
                latitude: 39.84,
                longitude: -82.8,
                accuracy: 10,
                altitude: null,
                altitudeAccuracy: null,
                heading: null,
                speed: null,
              },
              timestamp: Date.now(),
            } as GeolocationPosition);
          },
        },
      });
    });

    await page.goto("/");
    await page.waitForLoadState("networkidle");

    // First do GPS
    await page.locator("#locBtn").click();
    await page.waitForFunction(
      () => document.getElementById("stateLabel")?.textContent?.includes("Canal Winchester"),
      null,
      { timeout: 10000 }
    );

    // Now manually select a different state (California)
    await page.evaluate(() => {
      (window as any).selectState("CA");
    });

    // Wait for the label to change to California
    await page.waitForFunction(
      () => document.getElementById("stateLabel")?.textContent === "California",
      null,
      { timeout: 5000 }
    );

    const label = await page.locator("#stateLabel").textContent();
    expect(label).toBe("California");
    // Should NOT contain "Canal Winchester" anymore
    expect(label).not.toContain("Canal Winchester");
  });
});
