#!/usr/bin/env node
/**
 * Project lat/lng coordinates to AlbersUSA pixel space (975×610 viewport)
 * and output final per-state JSON files for the map prototype.
 *
 * Reads: scripts/intermediate/{STATE}.json (lat/lng from extract-state-data.py)
 * Outputs: public/data/{STATE}.json (x/y pixel coordinates)
 */

import { geoAlbersUsa } from 'd3-geo';
import { readFileSync, writeFileSync, mkdirSync, readdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const INPUT_DIR = join(__dirname, 'intermediate');
const OUTPUT_DIR = join(__dirname, '..', 'public', 'data');

mkdirSync(OUTPUT_DIR, { recursive: true });

// The us-atlas states-albers-10m.json uses the default geoAlbersUsa projection
// which maps to a 975×610 viewport
const projection = geoAlbersUsa();

function projectPoint(lon, lat) {
  const result = projection([lon, lat]);
  if (!result) return null; // Outside projection bounds (e.g. territories)
  return { x: Math.round(result[0] * 10) / 10, y: Math.round(result[1] * 10) / 10 };
}

function processState(filename) {
  const inputPath = join(INPUT_DIR, filename);
  const data = JSON.parse(readFileSync(inputPath, 'utf-8'));
  const stateCode = data.state;

  console.log(`Processing ${stateCode} (${data.stateName})...`);

  // Project schools
  const schools = [];
  let skipped = 0;
  for (const s of data.schools) {
    const pt = projectPoint(s.lon, s.lat);
    if (!pt) { skipped++; continue; }
    schools.push({
      name: s.name,
      x: pt.x,
      y: pt.y,
      proficiency: s.proficiency,
      district: s.district,
      city: s.city
    });
  }
  if (skipped) console.log(`  Skipped ${skipped} schools outside projection bounds`);

  // Project districts
  const districts = [];
  for (const d of data.districts) {
    const pt = projectPoint(d.lon, d.lat);
    if (!pt) continue;
    districts.push({
      name: d.name,
      x: pt.x,
      y: pt.y,
      proficiency: d.proficiency,
      schoolCount: d.schoolCount
    });
  }

  // Project cities
  const cities = [];
  for (const c of data.cities) {
    const pt = projectPoint(c.lon, c.lat);
    if (!pt) continue;
    cities.push({
      name: c.name,
      x: pt.x,
      y: pt.y,
      lat: c.lat,
      lon: c.lon,
      proficiency: c.proficiency,
      schoolCount: c.schoolCount
    });
  }

  const output = {
    state: stateCode,
    stateName: data.stateName,
    naep: data.naep,
    schools,
    districts,
    cities
  };

  const outPath = join(OUTPUT_DIR, `${stateCode}.json`);
  writeFileSync(outPath, JSON.stringify(output));
  console.log(`  → ${outPath} (${schools.length} schools, ${districts.length} districts, ${cities.length} cities)`);

  return output;
}

// Process all intermediate files
const files = readdirSync(INPUT_DIR).filter(f => f.endsWith('.json'));
console.log(`Found ${files.length} state files to process\n`);

for (const f of files) {
  processState(f);
}

console.log('\nDone! State data files are in public/data/');
