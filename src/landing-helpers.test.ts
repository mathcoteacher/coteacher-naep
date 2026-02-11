import { describe, it, expect } from "vitest";
import {
  buildExploreHref,
  extractLocationLabel,
  proficiencyToFraction,
} from "./landing-helpers";

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
  // Real BigDataCloud API response for Canal Winchester, OH (39.84, -82.80)
  const canalWinchesterFixture = {
    city: "Township of Madison",
    locality: "Canal Winchester",
    principalSubdivision: "Ohio",
    principalSubdivisionCode: "US-OH",
    localityInfo: {
      administrative: [
        { name: "United States of America (the)", order: 2, adminLevel: 2 },
        { name: "Ohio", order: 6, adminLevel: 4 },
        { name: "Fairfield County", order: 7, adminLevel: 6 },
        { name: "Township of Madison", order: 8, adminLevel: 7 },
        { name: "Township of Violet", order: 10, adminLevel: 7 },
        { name: "Canal Winchester", order: 12, adminLevel: 8 },
      ],
      informative: [
        { name: "North America", order: 1 },
        { name: "contiguous United States", order: 3 },
        { name: "Central Lowlands", order: 4 },
        { name: "America/New_York", description: "time zone", order: 5 },
        { name: "43110", description: "postal code", order: 9 },
        { name: "39-045-80206", description: "FIPS code", order: 11 },
      ],
    },
  };

  it("extracts Canal Winchester from real API response (not FIPS code)", () => {
    expect(extractLocationLabel(canalWinchesterFixture)).toBe("Canal Winchester");
  });

  it("never returns a FIPS code as a city name", () => {
    const label = extractLocationLabel(canalWinchesterFixture);
    expect(label).not.toMatch(/^\d/);
  });

  it("never returns a postal code as a city name", () => {
    const label = extractLocationLabel(canalWinchesterFixture);
    expect(label).not.toBe("43110");
  });

  it("never returns a timezone as a city name", () => {
    const label = extractLocationLabel(canalWinchesterFixture);
    expect(label).not.toBe("America/New_York");
  });

  it("uses city field when locality is admin-named", () => {
    const label = extractLocationLabel({
      city: "Columbus",
      locality: "Township of Franklin",
      principalSubdivision: "Ohio",
    });
    expect(label).toBe("Columbus");
  });

  it("skips city field when city is also admin-named, falls to administrative", () => {
    const label = extractLocationLabel({
      city: "Township of Madison",
      locality: "Township of Violet",
      principalSubdivision: "Ohio",
      localityInfo: {
        administrative: [
          { name: "Canal Winchester", order: 12, adminLevel: 8 },
        ],
      },
    });
    expect(label).toBe("Canal Winchester");
  });

  it("falls back to principalSubdivision when no city-level data", () => {
    const label = extractLocationLabel({
      city: "",
      locality: "",
      principalSubdivision: "Ohio",
      localityInfo: { informative: [] },
    });
    expect(label).toBe("Ohio");
  });

  it("returns empty string when no data available", () => {
    expect(extractLocationLabel({})).toBe("");
  });

  it("filters County admin patterns from locality", () => {
    const label = extractLocationLabel({
      city: "",
      locality: "County of Los Angeles",
      principalSubdivision: "California",
    });
    expect(label).toBe("California");
  });

  it("uses locality directly for normal city names", () => {
    const label = extractLocationLabel({
      city: "Township of Something",
      locality: "Springfield",
      principalSubdivision: "Ohio",
    });
    expect(label).toBe("Springfield");
  });
});

describe("proficiencyToFraction", () => {
  it("Canal Winchester: 0.615 proficiency → 4 out of 10", () => {
    const result = proficiencyToFraction(0.615);
    expect(result).toEqual({ numerator: 4, denominator: 10 });
  });

  it("50% proficiency → 5 out of 10 (prefers larger denominator on ties)", () => {
    const result = proficiencyToFraction(0.5);
    // belowProficient = 0.5 → exact fit at den=4 (2/4) and den=10 (5/10), prefers 10
    expect(result).toEqual({ numerator: 5, denominator: 10 });
  });

  it("75% proficiency → 1 out of 4", () => {
    const result = proficiencyToFraction(0.75);
    expect(result).toEqual({ numerator: 1, denominator: 4 });
  });

  it("20% proficiency → 8 out of 10 (prefers larger denominator on ties)", () => {
    const result = proficiencyToFraction(0.2);
    // belowProficient = 0.8 → exact fit at den=5 (4/5) and den=10 (8/10), prefers 10
    expect(result).toEqual({ numerator: 8, denominator: 10 });
  });

  it("clamps to avoid 0/Y for very high proficiency", () => {
    const result = proficiencyToFraction(0.99);
    expect(result.numerator).toBeGreaterThanOrEqual(1);
  });

  it("clamps to avoid Y/Y for very low proficiency", () => {
    const result = proficiencyToFraction(0.01);
    expect(result.numerator).toBeLessThan(result.denominator);
  });

  it("0% proficiency → clamps to (den-1)/den", () => {
    const result = proficiencyToFraction(0);
    expect(result.numerator).toBe(result.denominator - 1);
  });

  it("100% proficiency → clamps to 1/den", () => {
    const result = proficiencyToFraction(1);
    expect(result.numerator).toBe(1);
  });
});
