import { describe, it, expect } from "vitest";
import { buildExploreHref, extractLocationLabel } from "./landing-helpers";

describe("buildExploreHref", () => {
  it("returns /explore.html with no params when state is null", () => {
    expect(buildExploreHref(null)).toBe("/explore.html");
  });

  it("includes state param when state is provided", () => {
    expect(buildExploreHref("OH")).toBe("/explore.html?state=OH");
  });

  it("includes lat, lon, and city when all provided", () => {
    const href = buildExploreHref("OH", 39.9, -82.8, "Canal Winchester");
    expect(href).toBe(
      "/explore.html?state=OH&lat=39.9&lon=-82.8&city=Canal+Winchester"
    );
  });

  it("omits lat/lon when only lat is provided (both required)", () => {
    const href = buildExploreHref("OH", 39.9);
    expect(href).toBe("/explore.html?state=OH");
  });

  it("omits city when not provided", () => {
    const href = buildExploreHref("OH", 39.9, -82.8);
    expect(href).toBe("/explore.html?state=OH&lat=39.9&lon=-82.8");
  });
});

describe("extractLocationLabel", () => {
  it("extracts Canal Winchester from informative entries (Canal Winchester fixture)", () => {
    const label = extractLocationLabel({
      city: "",
      locality: "Township of Madison",
      principalSubdivision: "Ohio",
      principalSubdivisionCode: "US-OH",
      localityInfo: {
        informative: [
          { name: "Canal Winchester", order: 5, description: "populated place" },
        ],
      },
    });
    expect(label).toBe("Canal Winchester");
  });

  it("filters out Township names from informative entries", () => {
    const label = extractLocationLabel({
      city: "",
      locality: "Township of Madison",
      principalSubdivision: "Ohio",
      localityInfo: {
        informative: [
          { name: "Township of Madison", order: 4, description: "civil township" },
          { name: "Canal Winchester", order: 5, description: "populated place" },
        ],
      },
    });
    expect(label).toBe("Canal Winchester");
  });

  it("falls back to city when no informative entries match", () => {
    const label = extractLocationLabel({
      city: "Columbus",
      locality: "Township of Franklin",
      principalSubdivision: "Ohio",
      localityInfo: { informative: [] },
    });
    expect(label).toBe("Columbus");
  });

  it("falls back to locality when city is empty and locality is not admin", () => {
    const label = extractLocationLabel({
      city: "",
      locality: "Springfield",
      principalSubdivision: "Ohio",
      localityInfo: { informative: [] },
    });
    expect(label).toBe("Springfield");
  });

  it("skips locality when it matches admin pattern", () => {
    const label = extractLocationLabel({
      city: "",
      locality: "Township of Madison",
      principalSubdivision: "Ohio",
      localityInfo: { informative: [] },
    });
    expect(label).toBe("Ohio");
  });

  it("falls back to principalSubdivision as last resort", () => {
    const label = extractLocationLabel({
      principalSubdivision: "Ohio",
    });
    expect(label).toBe("Ohio");
  });

  it("returns empty string when no data available", () => {
    const label = extractLocationLabel({});
    expect(label).toBe("");
  });

  it("filters County admin patterns", () => {
    const label = extractLocationLabel({
      city: "",
      locality: "County of Los Angeles",
      principalSubdivision: "California",
      localityInfo: { informative: [] },
    });
    expect(label).toBe("California");
  });

  it("picks highest order informative entry when multiple qualify", () => {
    const label = extractLocationLabel({
      city: "",
      locality: "",
      principalSubdivision: "Ohio",
      localityInfo: {
        informative: [
          { name: "Franklin County", order: 4, description: "county" },
          { name: "Canal Winchester", order: 6, description: "populated place" },
          { name: "Groveport", order: 5, description: "populated place" },
        ],
      },
    });
    // Highest order (6) that isn't admin → Canal Winchester
    // "Franklin County" starts with admin-like name but doesn't match ADMIN_PATTERN
    // because it starts with "Franklin" not "County"
    // Actually "Franklin County" doesn't start with an admin keyword, so it passes filter.
    // But Canal Winchester has highest order (6), so it wins.
    expect(label).toBe("Canal Winchester");
  });
});
