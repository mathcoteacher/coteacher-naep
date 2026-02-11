#!/usr/bin/env node
/**
 * Builds a search index from all state data files.
 * Outputs public/data/search-index.json with entries for every city, district, and school.
 *
 * Usage: node scripts/build-search-index.js
 */

import { readFileSync, writeFileSync, readdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const dataDir = join(__dirname, '..', 'public', 'data');
const outPath = join(dataDir, 'search-index.json');

const STATE_NAMES = {
  AL:'Alabama',AK:'Alaska',AZ:'Arizona',AR:'Arkansas',CA:'California',
  CO:'Colorado',CT:'Connecticut',DE:'Delaware',DC:'District of Columbia',
  FL:'Florida',GA:'Georgia',HI:'Hawaii',ID:'Idaho',IL:'Illinois',
  IN:'Indiana',IA:'Iowa',KS:'Kansas',KY:'Kentucky',LA:'Louisiana',
  ME:'Maine',MD:'Maryland',MA:'Massachusetts',MI:'Michigan',MN:'Minnesota',
  MS:'Mississippi',MO:'Missouri',MT:'Montana',NE:'Nebraska',NV:'Nevada',
  NH:'New Hampshire',NJ:'New Jersey',NM:'New Mexico',NY:'New York',
  NC:'North Carolina',ND:'North Dakota',OH:'Ohio',OK:'Oklahoma',OR:'Oregon',
  PA:'Pennsylvania',RI:'Rhode Island',SC:'South Carolina',SD:'South Dakota',
  TN:'Tennessee',TX:'Texas',UT:'Utah',VT:'Vermont',VA:'Virginia',
  WA:'Washington',WV:'West Virginia',WI:'Wisconsin',WY:'Wyoming'
};

const index = [];

const files = readdirSync(dataDir).filter(f => /^[A-Z]{2}\.json$/.test(f));

for (const file of files) {
  const code = file.replace('.json', '');
  const data = JSON.parse(readFileSync(join(dataDir, file), 'utf-8'));
  const stateName = STATE_NAMES[code] || code;

  for (const c of (data.cities || [])) {
    index.push({ n: c.name, s: code, sn: stateName, t: 'city', x: c.x, y: c.y });
  }
  for (const d of (data.districts || [])) {
    index.push({ n: d.name, s: code, sn: stateName, t: 'district', x: d.x, y: d.y });
  }
  for (const s of (data.schools || [])) {
    index.push({ n: s.name, s: code, sn: stateName, t: 'school', x: s.x, y: s.y });
  }
}

// Sort alphabetically for consistent output
index.sort((a, b) => a.n.localeCompare(b.n));

writeFileSync(outPath, JSON.stringify(index));
console.log(`Search index: ${index.length} entries written to ${outPath}`);
