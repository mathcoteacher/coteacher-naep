import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

// Block external API calls so tests are deterministic (IP geo, BigDataCloud)
test.beforeEach(async ({ page }) => {
  // Block IP geolocation APIs so they don't affect state
  await page.route("**/ipapi.co/**", (route) => route.abort());
  await page.route("**/ipwho.is/**", (route) => route.abort());
});

function getStateBBoxFromTopology(fips: string) {
  const topology = JSON.parse(
    readFileSync(join(process.cwd(), "public/us-states.json"), "utf-8")
  ) as any;
  const transform = topology.transform || { scale: [1, 1], translate: [0, 0] };
  const scale = transform.scale as [number, number];
  const translate = transform.translate as [number, number];

  const decoded = (topology.arcs as Array<Array<[number, number]>>).map((arc) => {
    let x = 0,
      y = 0;
    return arc.map((p) => {
      x += p[0];
      y += p[1];
      return [x * scale[0] + translate[0], y * scale[1] + translate[1]] as [
        number,
        number
      ];
    });
  });

  const ring = (idx: number[]) => {
    const coords: Array<[number, number]> = [];
    for (const i of idx) {
      const arc = i >= 0 ? decoded[i] : decoded[~i].slice().reverse();
      for (let j = 0; j < arc.length; j++) {
        if (j > 0 || coords.length === 0) coords.push(arc[j]);
      }
    }
    return coords;
  };

  const geom = (topology.objects.states.geometries as any[]).find((g) => g.id === fips);
  if (!geom) throw new Error(`Missing geometry for FIPS ${fips}`);
  const polys =
    geom.type === "MultiPolygon"
      ? (geom.arcs as number[][][]).map((a) => a.map(ring))
      : [(geom.arcs as number[][]).map(ring)];

  let x0 = Infinity,
    y0 = Infinity,
    x1 = -Infinity,
    y1 = -Infinity;
  for (const poly of polys) {
    for (const r of poly) {
      for (const [x, y] of r) {
        if (x < x0) x0 = x;
        if (y < y0) y0 = y;
        if (x > x1) x1 = x;
        if (y > y1) y1 = y;
      }
    }
  }
  return { x0, y0, x1, y1, w: x1 - x0, h: y1 - y0 };
}

function getExpectedAlignedCityPoint(stateCode: string, fips: string, cityName: string) {
  const stateData = JSON.parse(
    readFileSync(join(process.cwd(), `public/data/${stateCode}.json`), "utf-8")
  ) as any;
  const city = (stateData.cities as any[]).find(
    (c) => (c.name as string).toLowerCase() === cityName.toLowerCase()
  );
  if (!city) throw new Error(`Missing city "${cityName}" in ${stateCode}.json`);

  const allPts = [
    ...(stateData.cities || []),
    ...(stateData.districts || []),
    ...(stateData.schools || []),
  ].filter((p: any) => Number.isFinite(p.x) && Number.isFinite(p.y));
  if (!allPts.length) throw new Error(`No points in ${stateCode}.json`);

  let dx0 = Infinity,
    dy0 = Infinity,
    dx1 = -Infinity,
    dy1 = -Infinity;
  for (const p of allPts) {
    if (p.x < dx0) dx0 = p.x;
    if (p.y < dy0) dy0 = p.y;
    if (p.x > dx1) dx1 = p.x;
    if (p.y > dy1) dy1 = p.y;
  }

  const bb = getStateBBoxFromTopology(fips);
  const dw = dx1 - dx0;
  const dh = dy1 - dy0;
  const pad = 0.04;
  const availW = bb.w * (1 - 2 * pad);
  const availH = bb.h * (1 - 2 * pad);
  const alignScale = Math.min(availW / dw, availH / dh);
  const scaledW = dw * alignScale;
  const scaledH = dh * alignScale;
  const ox = bb.x0 + bb.w * pad + (availW - scaledW) / 2;
  const oy = bb.y0 + bb.h * pad + (availH - scaledH) / 2;

  return {
    x: ox + (city.x - dx0) * alignScale,
    y: oy + (city.y - dy0) * alignScale,
  };
}

const CANAL_WINCHESTER_POINT = getExpectedAlignedCityPoint(
  "OH",
  "39",
  "Canal Winchester"
);

async function mockCanalWinchesterGps(page: Page) {
  await page.route("**/api.bigdatacloud.net/**", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        city: "Township of Madison",
        locality: "Canal Winchester",
        principalSubdivision: "Ohio",
        principalSubdivisionCode: "US-OH",
        localityInfo: {
          administrative: [
            { name: "Ohio", order: 6, adminLevel: 4 },
            { name: "Fairfield County", order: 7, adminLevel: 6 },
            { name: "Township of Madison", order: 8, adminLevel: 7 },
            { name: "Canal Winchester", order: 12, adminLevel: 8 },
          ],
          informative: [
            { name: "Central Lowlands", order: 4 },
            { name: "America/New_York", description: "time zone", order: 5 },
            { name: "43110", description: "postal code", order: 9 },
            { name: "39-045-80206", description: "FIPS code", order: 11 },
          ],
        },
      }),
    })
  );

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
}

// ============================================================
// Test Group A: Page Structure & CTAs
// ============================================================

test.describe("Page Structure & CTAs", () => {
  test('primary CTA "Math CoTeacher is the solution" exists inside .story-side', async ({
    page,
  }) => {
    await page.goto("/");
    const cta = page.locator(".story-side .cta-button");
    await expect(cta).toBeVisible();
    await expect(cta).toHaveText("Math CoTeacher is the solution");
  });

  test('"Explore the map" exists inside .map-side with .explore-link class', async ({
    page,
  }) => {
    await page.goto("/");
    const explore = page.locator(".map-side .explore-link");
    await expect(explore).toBeVisible();
    await expect(explore).toContainText("Explore the map");
  });

  test('"Explore the data" does NOT have .cta-button class (secondary styling)', async ({
    page,
  }) => {
    await page.goto("/");
    const explore = page.locator(".map-side .explore-link");
    // It should NOT also have the cta-button class
    await expect(explore).not.toHaveClass(/cta-button/);
  });

  test("desktop: Use my location is above map and Explore the map is centered below map", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.goto("/");
    await expect(page.locator("#locBtn")).toBeVisible();

    const box = await page.evaluate(() => {
      const mapRect = document.getElementById("mapWrapper")?.getBoundingClientRect();
      const locRect = document.getElementById("locBtn")?.getBoundingClientRect();
      const exploreRect = document.getElementById("exploreBtn")?.getBoundingClientRect();
      if (!mapRect || !locRect || !exploreRect) return null;
      return {
        map: { left: mapRect.left, top: mapRect.top, width: mapRect.width, height: mapRect.height, bottom: mapRect.bottom },
        loc: { bottom: locRect.bottom },
        explore: { left: exploreRect.left, width: exploreRect.width, top: exploreRect.top }
      };
    });

    expect(box).not.toBeNull();
    expect(box!.loc.bottom).toBeLessThan(box!.map.top + 2);
    expect(box!.explore.top).toBeGreaterThan(box!.map.bottom - 2);
    const mapCenterX = box!.map.left + box!.map.width / 2;
    const exploreCenterX = box!.explore.left + box!.explore.width / 2;
    expect(Math.abs(exploreCenterX - mapCenterX)).toBeLessThan(6);
  });

  test("mobile: Use my location is above map and Explore the map is centered below map", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(page.locator("#locBtn")).toBeVisible();

    const box = await page.evaluate(() => {
      const mapRect = document.getElementById("mapWrapper")?.getBoundingClientRect();
      const locRect = document.getElementById("locBtn")?.getBoundingClientRect();
      const exploreRect = document.getElementById("exploreBtn")?.getBoundingClientRect();
      if (!mapRect || !locRect || !exploreRect) return null;
      return {
        map: { left: mapRect.left, top: mapRect.top, width: mapRect.width, height: mapRect.height, bottom: mapRect.bottom },
        loc: { bottom: locRect.bottom },
        explore: { left: exploreRect.left, width: exploreRect.width, top: exploreRect.top }
      };
    });

    expect(box).not.toBeNull();
    expect(box!.loc.bottom).toBeLessThan(box!.map.top + 2);
    expect(box!.explore.top).toBeGreaterThan(box!.map.bottom - 2);
    const mapCenterX = box!.map.left + box!.map.width / 2;
    const exploreCenterX = box!.explore.left + box!.explore.width / 2;
    expect(Math.abs(exploreCenterX - mapCenterX)).toBeLessThan(6);
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
// Test Group B2: Explore Search Selection
// ============================================================

test.describe("Explore Search Selection", () => {
  test("selecting Dublin (Ohio) updates story to city proficiency and highlights the exact city dot", async ({
    page,
  }) => {
    await page.goto("/explore.html?state=OH");
    await page.waitForLoadState("networkidle");

    const input = page.locator("#searchInput");
    await input.fill("Dublin");

    const result = page
      .locator(".search-result")
      .filter({ hasText: "Dublin" })
      .filter({ hasText: "Ohio" })
      .first();
    await expect(result).toBeVisible();
    await result.click();

    await page.waitForFunction(
      () => document.getElementById("stateLabel")?.textContent === "Dublin",
      null,
      { timeout: 10000 }
    );

    await expect(page.locator("#numText")).toHaveText("7");
    await expect(page.locator("#denomText")).toHaveText("10");

    await page.waitForFunction(() => {
      const selected = Array.from(document.querySelectorAll(".data-point.selected-target"));
      return selected.some((el) => {
        const label = el.querySelector(".pt-label")?.textContent?.trim();
        const display = (el as HTMLElement).style.display;
        return label === "Dublin" && display !== "none";
      });
    });
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

  test("GPS geolocation updates headline to city-only with city-level proficiency", async ({
    page,
  }) => {
    // Block IP geo
    await page.route("**/ipapi.co/**", (route) => route.abort());

    // Mock BigDataCloud reverse geocoding (real Canal Winchester API response)
    await page.route("**/api.bigdatacloud.net/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          city: "Township of Madison",
          locality: "Canal Winchester",
          principalSubdivision: "Ohio",
          principalSubdivisionCode: "US-OH",
          localityInfo: {
            administrative: [
              { name: "Ohio", order: 6, adminLevel: 4 },
              { name: "Fairfield County", order: 7, adminLevel: 6 },
              { name: "Township of Madison", order: 8, adminLevel: 7 },
              { name: "Canal Winchester", order: 12, adminLevel: 8 },
            ],
            informative: [
              { name: "Central Lowlands", order: 4 },
              { name: "America/New_York", description: "time zone", order: 5 },
              { name: "43110", description: "postal code", order: 9 },
              { name: "39-045-80206", description: "FIPS code", order: 11 },
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
    expect(btnText).toBe("My location: Canal Winchester");

    // Verify headline uses city only
    const label = await page.locator("#stateLabel").textContent();
    expect(label).toBe("Canal Winchester");

    // Verify city-level proficiency numbers (Canal Winchester = 0.615 → 4 out of 10)
    const numText = await page.locator("#numText").textContent();
    const denomText = await page.locator("#denomText").textContent();
    expect(numText).toBe("4");
    expect(denomText).toBe("10");
  });

  test("GPS geolocation falls back to state NAEP for unknown city", async ({
    page,
  }) => {
    // Block IP geo
    await page.route("**/ipapi.co/**", (route) => route.abort());

    // Mock BigDataCloud with an unknown city
    await page.route("**/api.bigdatacloud.net/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          city: "",
          locality: "Nowheresville",
          principalSubdivision: "Ohio",
          principalSubdivisionCode: "US-OH",
          localityInfo: { administrative: [], informative: [] },
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

    const locBtn = page.locator("#locBtn");
    await locBtn.click();

    // Wait for button to update
    await page.waitForFunction(
      () => document.getElementById("locBtn")?.textContent?.includes("Nowheresville"),
      null,
      { timeout: 10000 }
    );

    // Should fall back to Ohio state NAEP numbers (7 out of 10)
    const numText = await page.locator("#numText").textContent();
    const denomText = await page.locator("#denomText").textContent();
    expect(numText).toBe("7");
    expect(denomText).toBe("10");
  });

  test("manual state click after GPS resets to state-only label", async ({
    page,
  }) => {
    // Mock BigDataCloud (real Canal Winchester API response)
    await page.route("**/api.bigdatacloud.net/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          city: "Township of Madison",
          locality: "Canal Winchester",
          principalSubdivision: "Ohio",
          principalSubdivisionCode: "US-OH",
          localityInfo: {
            administrative: [
              { name: "Ohio", order: 6, adminLevel: 4 },
              { name: "Fairfield County", order: 7, adminLevel: 6 },
              { name: "Township of Madison", order: 8, adminLevel: 7 },
              { name: "Canal Winchester", order: 12, adminLevel: 8 },
            ],
            informative: [
              { name: "Central Lowlands", order: 4 },
              { name: "America/New_York", description: "time zone", order: 5 },
              { name: "43110", description: "postal code", order: 9 },
              { name: "39-045-80206", description: "FIPS code", order: 11 },
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

// ============================================================
// Test Group D: GPS Pin Placement
// ============================================================

test.describe("GPS Pin Placement", () => {
  test("drops Canal Winchester pin at expected map location on desktop", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1400, height: 900 });
    await mockCanalWinchesterGps(page);

    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.locator("#locBtn").click();

    const pin = page.locator("#pinLayer .city-pin");
    await expect(pin).toBeVisible();

    const pos = await page.evaluate(
      ({ x, y }) => {
        const wrapper = document.getElementById("mapWrapper");
        const pinEl = document.querySelector("#pinLayer .city-pin") as HTMLElement | null;
        if (!wrapper || !pinEl) return null;
        const scale = wrapper.clientWidth / 975;
        return {
          actualLeft: parseFloat(pinEl.style.left),
          actualTop: parseFloat(pinEl.style.top),
          expectedLeft: x * scale,
          expectedTop: y * scale,
        };
      },
      { x: CANAL_WINCHESTER_POINT.x, y: CANAL_WINCHESTER_POINT.y }
    );

    expect(pos).not.toBeNull();
    expect(Number.isFinite(pos!.actualLeft)).toBe(true);
    expect(Number.isFinite(pos!.actualTop)).toBe(true);
    expect(Math.abs(pos!.actualLeft - pos!.expectedLeft)).toBeLessThan(3);
    expect(Math.abs(pos!.actualTop - pos!.expectedTop)).toBeLessThan(3);
  });

  test("drops Canal Winchester pin at expected map location on mobile", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockCanalWinchesterGps(page);

    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.locator("#locBtn").click();

    const pin = page.locator("#pinLayer .city-pin");
    await expect(pin).toBeVisible();

    const pos = await page.evaluate(
      ({ x, y }) => {
        const wrapper = document.getElementById("mapWrapper");
        const pinEl = document.querySelector("#pinLayer .city-pin") as HTMLElement | null;
        if (!wrapper || !pinEl) return null;
        const scale = wrapper.clientWidth / 975;
        return {
          actualLeft: parseFloat(pinEl.style.left),
          actualTop: parseFloat(pinEl.style.top),
          expectedLeft: x * scale,
          expectedTop: y * scale,
        };
      },
      { x: CANAL_WINCHESTER_POINT.x, y: CANAL_WINCHESTER_POINT.y }
    );

    expect(pos).not.toBeNull();
    expect(Number.isFinite(pos!.actualLeft)).toBe(true);
    expect(Number.isFinite(pos!.actualTop)).toBe(true);
    expect(Math.abs(pos!.actualLeft - pos!.expectedLeft)).toBeLessThan(3);
    expect(Math.abs(pos!.actualTop - pos!.expectedTop)).toBeLessThan(3);
  });

  test("keeps pin aligned after viewport resize", async ({ page }) => {
    await page.setViewportSize({ width: 1400, height: 900 });
    await mockCanalWinchesterGps(page);

    await page.goto("/");
    await page.waitForLoadState("networkidle");
    await page.locator("#locBtn").click();
    await expect(page.locator("#pinLayer .city-pin")).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(100);

    const pos = await page.evaluate(
      ({ x, y }) => {
        const wrapper = document.getElementById("mapWrapper");
        const pinEl = document.querySelector("#pinLayer .city-pin") as HTMLElement | null;
        if (!wrapper || !pinEl) return null;
        const scale = wrapper.clientWidth / 975;
        return {
          actualLeft: parseFloat(pinEl.style.left),
          actualTop: parseFloat(pinEl.style.top),
          expectedLeft: x * scale,
          expectedTop: y * scale,
        };
      },
      { x: CANAL_WINCHESTER_POINT.x, y: CANAL_WINCHESTER_POINT.y }
    );

    expect(pos).not.toBeNull();
    expect(Math.abs(pos!.actualLeft - pos!.expectedLeft)).toBeLessThan(3);
    expect(Math.abs(pos!.actualTop - pos!.expectedTop)).toBeLessThan(3);
  });
});
