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
const SAIPE_PATH = join(__dirname, 'saipe_data.json');

mkdirSync(OUTPUT_DIR, { recursive: true });

// Load Census poverty data (LEAID → poverty rate %)
let saipeData = {};
try {
  saipeData = JSON.parse(readFileSync(SAIPE_PATH, 'utf-8'));
  console.log(`Loaded poverty data for ${Object.keys(saipeData).length} districts\n`);
} catch (e) {
  console.log('Warning: saipe_data.json not found — run python3 scripts/saipe.py first\n');
}

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

  // Build district aggregates keyed by LEAID when available.
  // This avoids district-name collisions (same name, different LEAID).
  const districtAgg = new Map();

  // Project schools
  const schools = [];
  let skipped = 0;
  for (const s of data.schools) {
    const pt = projectPoint(s.lon, s.lat);
    if (!pt) { skipped++; continue; }
    const rec = {
      name: s.name,
      x: pt.x,
      y: pt.y,
      proficiency: s.proficiency,
      district: s.district,
      city: s.city
    };
    let leaid = null;
    if (s.ncessch) {
      leaid = s.ncessch.substring(0, 7);
      rec.leaid = leaid;
      const pov = saipeData[leaid];
      if (pov != null) rec.povertyRate = pov;
    }
    schools.push(rec);

    const districtName = s.district || '';
    if (!districtName) continue;
    const districtKey = leaid ? `leaid:${leaid}` : `name:${districtName}`;
    let agg = districtAgg.get(districtKey);
    if (!agg) {
      agg = {
        name: districtName,
        leaid,
        schoolCount: 0,
        sumLat: 0,
        sumLon: 0,
        sumProf: 0
      };
      districtAgg.set(districtKey, agg);
    }
    agg.schoolCount += 1;
    agg.sumLat += s.lat;
    agg.sumLon += s.lon;
    agg.sumProf += s.proficiency;
  }
  if (skipped) console.log(`  Skipped ${skipped} schools outside projection bounds`);

  // Project districts from school-derived LEAID aggregates.
  const districts = [];
  for (const agg of districtAgg.values()) {
    const avgLat = agg.sumLat / agg.schoolCount;
    const avgLon = agg.sumLon / agg.schoolCount;
    const pt = projectPoint(avgLon, avgLat);
    if (!pt) continue;
    const rec = {
      name: agg.name,
      x: pt.x,
      y: pt.y,
      proficiency: Math.round((agg.sumProf / agg.schoolCount) * 10000) / 10000,
      schoolCount: agg.schoolCount
    };
    if (agg.leaid) {
      rec.leaid = agg.leaid;
      const pov = saipeData[agg.leaid];
      if (pov != null) rec.povertyRate = pov;
    }
    districts.push(rec);
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
