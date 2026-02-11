# WARP.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

Project: NAEP Geo — Cloudflare Worker serving a zero-JS HTML UI with static assets.

Commands
- Install deps
  - npm i
- Develop locally (Cloudflare Workers)
  - npm run dev
  - Use real GeoIP during dev: npm run dev -- --remote
- Typecheck
  - npm run typecheck
- Tests (Vitest + Miniflare)
  - UI runner: npm run test
  - Headless: npm run test:run
  - Run a single test file: npm run test:run -- src/worker.test.ts
  - Run by name: npm run test:run -- -t "homepage"
- Deploy (Cloudflare)
  - npm run publish

Architecture overview
- Platform: Cloudflare Worker (module syntax) with static asset binding.
  - Entry: src/worker.ts
  - Config: wrangler.jsonc
    - main: src/worker.ts
    - assets.directory: public
    - assets.binding: ASSETS (available on env)
    - assets.run_worker_first: true (routes first hit the Worker, which then may read assets)
- Routing
  - GET / → renders home page, using location-aware NAEP data (state if available via request.cf.regionCode, else national)
  - GET /investor → renders investor notes page (noindex)
  - All other paths → 404 page
  - Non-GET methods → 405 ("Method Not Allowed")
- Data loading and caching
  - NAEP data is bundled as public/naep.json and served via the ASSETS binding
  - Worker loads it via env.ASSETS.fetch("https://assets.local/naep.json")
  - In-memory promise cache (per-worker instance) avoids repeated JSON parsing during hot lifetimes
  - JSON is validated strictly (shape and "X out of Y" text pattern) before use
- Location resolution
  - Uses Cloudflare-provided request.cf: { country, regionCode }
  - If country === "US" and a 2-letter state code exists in the dataset, the state’s value is used; otherwise national value
  - STATE_NAME map renders full state names for display
- Images
  - Optional illustrative images for known ratios (three, seven, eight, nine) in public/images/*.webp
  - Existence is checked via HEAD to the ASSETS binding (fallback to GET range on 405)
- HTML rendering
  - Pure server-rendered strings; no JavaScript shipped to clients
  - layoutHTML composes a minimal, responsive, accessible UI (light/dark) with Deep Indigo styling
  - homeHTML and investorHTML build page bodies; 404 has its own template
  - Strong security headers (CSP default-src 'none'; no external calls; nosniff; private, no-store caching)
- Testing
  - Vitest configured with Miniflare environment (vitest.config.ts)
  - SELF.fetch is used to exercise the Worker end-to-end
  - Assets are available to tests via environmentOptions.assets: "public"

Important files
- wrangler.jsonc: Worker and assets binding configuration
- src/worker.ts: Request handling, data loading, HTML templates, and security headers
- public/naep.json: Bundled NAEP dataset used at runtime
- public/images/*.webp: Optional images keyed by ratio name
- vitest.config.ts: Miniflare test environment with assets binding
- README.md: Quick deploy and dev notes

Notes
- For realistic geo during development, prefer npm run dev -- --remote so Cloudflare edge provides request.cf.
- No lint script is defined; rely on TypeScript strict typechecking and tests.
