# Quality-First Curriculum Coverage Plan (No Guesswork)

Date: 2026-02-13
Scope: K-5 and 6-8 math curriculum for all 13,248 districts

## Current Ground Truth
- Districts with any resolved data: 2,803 / 13,248 (21.2%)
- K-5 coverage: 2,580
- 6-8 coverage: 2,268
- Verified rows: 4,324
- Inferred rows: 524
- Major evidence sources: CEMD (3,948 rows), state dashboards (938 rows)
- District website extraction remains low-yield (15 rows)
- Board sources expanded to 1,988 URLs across 1,360 districts, but extraction yield is still low

## Non-Negotiable Quality Rules
- Do not claim 100% coverage unless both grade bands are evidence-backed for all districts.
- Do not auto-promote weak search hits to verified.
- Every verified row must have:
  - source URL
  - stored document/snippet evidence
  - district identity context
  - grade-band context
  - curriculum mention

## Execution Priorities
1. Build state-specific high-yield importers (district-level source data only)
- Target states with district-level instructional-material datasets or dashboards.
- For each state source:
  - ingest raw dataset/page
  - map district names to LEAID
  - insert extraction_candidates with source_type='state_dashboard' or 'state_doe'
  - store provenance URL and snippet
- Do not ingest state-level approved lists that do not identify district usage.

2. Strengthen board platform extraction with item-level evidence
- BoardDocs: move beyond term-hit IDs to item/packet text that includes the curriculum term and grade context.
- Keep board evidence disabled for verification until item-level text is captured.

3. Improve district web extraction precision+recall
- Add section-aware parsing (tables/lists/headings) for curriculum pages.
- Keep strict grade-band requirement, but support page-level structure only on curriculum/math URLs.
- Add a source-level wall-clock timeout to avoid long stalls.

4. Expand structured source registry
- Add district RFP/procurement portals only when district-linked and crawlable.
- Add known board platforms from district site discovery (already expanded).

## Acceptance Criteria Per Batch
- At least +250 verified rows per significant batch OR clear evidence that source has low signal.
- Validation must pass (`python3 curriculum/validate.py`).
- Export must run (`python3 curriculum/export.py`).
- Report delta:
  - verified rows change
  - districts with both bands change
  - source-type contribution

## Required Reporting Format
At end of each run, report:
- exact command(s)
- runtime
- processed sources
- evidence inserted by source_type
- verified/inferred delta
- top failure modes (with counts)

## Hard Stop Conditions
- If a source is not district-level, do not force inference from it.
- If evidence is missing snippets/doc links, do not mark verified.
- If coverage claims conflict with DB counts, fix claim and rerun checks.
