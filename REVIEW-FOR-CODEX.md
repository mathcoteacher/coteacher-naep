# Math CoTeacher Landing Page — Bug Fix & TDD Plan (v2, post-review)

**Date:** 2026-02-11
**Author:** Claude (Opus 4.6, working with Mark in Claude Code)
**Reviewed by:** Codex (acting as staff engineer)
**Status:** Plan accepted with required edits. Executing.

---

## Execution Plan (incorporating all Codex feedback)

### Phase 0: Baseline Test Recovery (COMPLETED)
- Installed `@cloudflare/vitest-pool-workers` (replaced broken `vitest-environment-miniflare`)
- Updated `vitest.config.ts` to use `defineWorkersConfig` with wrangler config
- Fixed `worker.test.ts` import: `miniflare:shared` → `cloudflare:test`
- **Result: `npm run test:run` passes 4/4 tests**

### Phase 1: Unit Tests for Extracted Pure Functions

Extract two pure functions from the landing page JS into a testable module:

#### `buildExploreHref(state, lat?, lon?, city?)`
Returns the canonical explore URL: `/explore.html` + query params.
- `buildExploreHref('OH')` → `/explore.html?state=OH`
- `buildExploreHref('OH', 39.9, -82.8, 'Canal Winchester')` → `/explore.html?state=OH&lat=39.9&lon=-82.8&city=Canal+Winchester`
- `buildExploreHref(null)` → `/explore.html`

#### `extractLocationLabel(geoResponse)`
Parses a BigDataCloud response and returns a human-friendly city name.

**Parsing priority (per Codex):** Do NOT trust `city` first. Instead:
1. Check `localityInfo.informative` for entries with `order` >= 4 that are NOT administrative names (filter out "Township of...", "County of..." patterns)
2. Fall back to `city` if populated
3. Fall back to `locality` only if it doesn't match admin patterns (Township, County, Borough, etc.)
4. Last resort: `principalSubdivision` (state name)

**Canal Winchester fixture test:**
```
Input: { city: "", locality: "Township of Madison", principalSubdivision: "Ohio",
         principalSubdivisionCode: "US-OH",
         localityInfo: { informative: [
           { name: "Canal Winchester", order: 5, description: "populated place" }
         ]}}
Expected output: "Canal Winchester"
```

### Phase 2: Playwright E2E Tests

**Critical: Tests run from `/` with rewrite behavior**, not `/prototypes/map-v3.html`.

Playwright config starts a local HTTP server that replicates Netlify's `_redirects` behavior:
- `/` serves `prototypes/map-v3.html` content (200 rewrite)
- `/explore.html` serves `prototypes/explore.html` content (200 rewrite)

#### Test Group A: Page Structure & CTAs (semantic/class assertions, not visual)
```
TEST: Primary CTA "Math CoTeacher is a solution" exists inside .story-side container
  → assert: element with class .cta-button inside .story-side contains text "Math CoTeacher"

TEST: "Explore the data" exists inside .map-side container (below map)
  → assert: element with class .explore-link inside .map-side contains text "Explore"
  → assert: .explore-link does NOT have class .cta-button (it's secondary, toolbar-styled)
```

#### Test Group B: Explore Link Navigation
```
TEST: Starting at /, clicking "Explore the data" navigates without 404
  → navigate to /
  → click the explore link
  → assert: page title contains "Explore" (not "Not Found")
  → assert: response status is 200

TEST: Explore link includes state param when a state is selected
  → navigate to /
  → trigger selectState('OH') via page.evaluate
  → assert: explore link href contains "state=OH"
```

#### Test Group C: Geolocation Label State Transitions

Three label modes with explicit transitions:

| Trigger | Label format | Example |
|---------|-------------|---------|
| `ip` (page load auto-detect) | State name | "Ohio" |
| `gps` (Use my location click) | City, State | "Canal Winchester, Ohio" |
| `manual` (click a state on map) | State name | "California" |

**Reset rule:** Manual click always resets to state-only label, even after GPS was used.

```
TEST: IP geolocation sets headline to state name only
  → mock IP geo to return region: 'OH'
  → assert: headline contains "Ohio" (not a city name)

TEST: GPS geolocation updates headline to city + state
  → mock navigator.geolocation + BigDataCloud API
  → click "Use my location"
  → assert: headline contains "Canal Winchester, Ohio"
  → assert: button text is "My location: Canal Winchester, Ohio"

TEST: Manual state click after GPS resets to state-only label
  → complete GPS flow (headline shows "Canal Winchester, Ohio")
  → click California on the map
  → assert: headline now contains "California" (not "Canal Winchester")
```

### Phase 3: Implementation

#### 3a. CTA Restructure (`map-v3.html`)
- **Story side:** Replace "Explore the data" with "Math CoTeacher is a solution" (primary CTA, `.cta-button` class, links to placeholder `#solution`)
- **Map side:** Add "Explore the data" below `.map-wrapper` as `.explore-link` with toolbar-button styling (bordered, understated — same style as "Use my location")
- Both link writers (state selection path AND GPS path) update the explore link href

#### 3b. Canonical Explore URL
- `_redirects` gets: `/explore.html /prototypes/explore.html 200`
- All href writers use `/explore.html?state=XX` (canonical URL, not relative path)
- `buildExploreHref()` is the single source of truth for this URL
- **Both `updateExploreLink()` and the GPS handler** call `buildExploreHref()`

#### 3c. Geocode Parsing + Headline Override
- `extractLocationLabel(geoResponse)` implements the priority chain described above
- `updateStoryFromNaep(code, locationLabel?)` accepts optional label override
- GPS path calls: `updateStoryFromNaep(detectedState, `${cityName}, ${stateName}`)`
- IP path calls: `updateStoryFromNaep(code)` (no override, uses state name)
- Manual click calls: `updateStoryFromNaep(code)` (no override, resets to state name)

### Phase 4: Full Test Suite Run
- `npm run test:run` (worker unit tests + landing page unit tests)
- `npx playwright test` (e2e tests)
- Report pass/fail summary

---

## Acceptance Gates

1. From `/`, clicking Explore reaches `prototypes/explore.html` (no 404)
2. GPS mock for Canal Winchester renders "My location: Canal Winchester, Ohio"
3. Headline uses state label for IP/manual and city+state for GPS
4. All tests pass in CI-equivalent `npm run test:run` plus e2e command
