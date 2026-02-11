import { SELF } from "cloudflare:test";
import { describe, it, expect } from "vitest";

describe("NAEP Geo Worker", () => {
  it("should return the homepage for the root path", async () => {
    const res = await SELF.fetch("http://localhost/");
    expect(res.status).toBe(200);

    const text = await res.text();
    expect(text).toContain("8th graders are below proficient in math");
    expect(text).toContain("Detected via IP");
  });

  it("should return the investor page for the /investor path", async () => {
    const res = await SELF.fetch("http://localhost/investor");
    expect(res.status).toBe(200);

    const text = await res.text();
    expect(text).toContain("Investor Overview");
    expect(text).toContain("This page is intentionally not linked");
  });

  it("should return a 404 for a non-existent page", async () => {
    const res = await SELF.fetch("http://localhost/this-page-does-not-exist");
    expect(res.status).toBe(404);

    const text = await res.text();
    expect(text).toContain("Not Found");
  });

  it("should return 405 for non-GET requests", async () => {
    const res = await SELF.fetch("http://localhost/", { method: "POST" });
    expect(res.status).toBe(405);
    expect(await res.text()).toBe("Method Not Allowed");
  });
});