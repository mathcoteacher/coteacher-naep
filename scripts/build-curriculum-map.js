#!/usr/bin/env node
/**
 * Build script: joins curriculum.json with district_centroids.json,
 * projects lat/lng to map x,y coordinates (Albers USA),
 * validates output, and writes public/data/curriculum-map.json.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const ROOT = path.join(__dirname, '..');
const curriculumPath = path.join(ROOT, 'public/data/curriculum.json');
const centroidsPath = path.join(ROOT, 'scripts/curriculum/seed_data/district_centroids.json');
const topoPath = path.join(ROOT, 'public/us-states.json');
const outputPath = path.join(ROOT, 'public/data/curriculum-map.json');

// ---- FIPS code table (state code → FIPS ID) ----
const FIPS = {
  AL:'01',AK:'02',AZ:'04',AR:'05',CA:'06',CO:'08',CT:'09',DE:'10',DC:'11',
  FL:'12',GA:'13',HI:'15',ID:'16',IL:'17',IN:'18',IA:'19',KS:'20',KY:'21',
  LA:'22',ME:'23',MD:'24',MA:'25',MI:'26',MN:'27',MS:'28',MO:'29',MT:'30',
  NE:'31',NV:'32',NH:'33',NJ:'34',NM:'35',NY:'36',NC:'37',ND:'38',OH:'39',
  OK:'40',OR:'41',PA:'42',RI:'44',SC:'45',SD:'46',TN:'47',TX:'48',UT:'49',
  VT:'50',VA:'51',WA:'53',WV:'54',WI:'55',WY:'56'
};

// ---- TopoJSON decoder (from explore.html) ----
function topoDecode(topology) {
  const { scale: s, translate: t } = topology.transform || { scale: [1,1], translate: [0,0] };
  function decArc(arc) { let x=0,y=0; return arc.map(p => { x+=p[0]; y+=p[1]; return [x*s[0]+t[0], y*s[1]+t[1]]; }); }
  const dec = topology.arcs.map(decArc);
  function ring(idx) { const c=[]; for(const i of idx){ const a=i>=0?dec[i]:dec[~i].slice().reverse(); for(let j=0;j<a.length;j++) if(j>0||c.length===0) c.push(a[j]); } return c; }
  return o => topology.objects[o].geometries.map(g => {
    if(g.type==='Polygon') return{type:'Polygon',coordinates:g.arcs.map(ring),properties:g.properties,id:g.id};
    if(g.type==='MultiPolygon') return{type:'MultiPolygon',coordinates:g.arcs.map(a=>a.map(ring)),properties:g.properties,id:g.id};
    return g;
  });
}

// ---- Compute bounding box from state geometry (from explore.html) ----
function getBBox(feature) {
  let x0=Infinity,y0=Infinity,x1=-Infinity,y1=-Infinity;
  const p = feature.type==='MultiPolygon'?feature.coordinates:[feature.coordinates];
  for(const poly of p) for(const ring of poly) for(const [x,y] of ring){
    if(x<x0)x0=x;if(y<y0)y0=y;if(x>x1)x1=x;if(y>y1)y1=y;
  }
  return {x0,y0,x1,y1,w:x1-x0,h:y1-y0};
}

// ---- Albers USA projection (matches explore.html exactly) ----
function projectionFromLatLon(lon, lat) {
  if (lat < 24 || lat > 50 || lon < -130 || lon > -65) {
    if (lat >= 18 && lat <= 23 && lon >= -161 && lon <= -154) {
      return [260 + (lon + 160) * 15, 520 + (20.5 - lat) * 15];
    }
    if (lat >= 51 && lat <= 72 && lon >= -180 && lon <= -129) {
      return [150 + (lon + 170) * 3.5, 490 + (65 - lat) * 5];
    }
    return null;
  }
  const lambda0 = -96 * Math.PI / 180;
  const phi0 = 37.5 * Math.PI / 180;
  const phi1 = 29.5 * Math.PI / 180;
  const phi2 = 45.5 * Math.PI / 180;
  const n = 0.5 * (Math.sin(phi1) + Math.sin(phi2));
  const C = Math.cos(phi1) ** 2 + 2 * n * Math.sin(phi1);
  const rho0 = Math.sqrt(C - 2 * n * Math.sin(phi0)) / n;
  const lambda = lon * Math.PI / 180;
  const phi = lat * Math.PI / 180;
  const rho = Math.sqrt(C - 2 * n * Math.sin(phi)) / n;
  const theta = n * (lambda - lambda0);
  const x = rho * Math.sin(theta);
  const y = rho0 - rho * Math.cos(theta);
  const scale = 1070;
  const tx = 487.5, ty = 305;
  return [x * scale + tx, -y * scale + ty];
}

// ---- Load data ----
console.log('Loading curriculum.json...');
const curriculum = JSON.parse(fs.readFileSync(curriculumPath, 'utf-8'));

console.log('Loading district_centroids.json...');
const centroids = JSON.parse(fs.readFileSync(centroidsPath, 'utf-8'));

// ---- Collect unique curriculum names ----
const k5Set = new Set();
const g68Set = new Set();
for (const d of Object.values(curriculum.districts)) {
  if (d.k5) k5Set.add(d.k5);
  if (d.g68) g68Set.add(d.g68);
}

// Sort by frequency (most common first) for better legend ordering
function sortByFreq(set, field) {
  const counts = {};
  for (const d of Object.values(curriculum.districts)) {
    if (d[field]) counts[d[field]] = (counts[d[field]] || 0) + 1;
  }
  return [...set].sort((a, b) => (counts[b] || 0) - (counts[a] || 0));
}

const curricula_k5 = sortByFreq(k5Set, 'k5');
const curricula_g68 = sortByFreq(g68Set, 'g68');
const k5Index = Object.fromEntries(curricula_k5.map((c, i) => [c, i]));
const g68Index = Object.fromEntries(curricula_g68.map((c, i) => [c, i]));

console.log(`Found ${curricula_k5.length} K-5 curricula, ${curricula_g68.length} 6-8 curricula`);

// ---- Join and project ----
const districts = [];
let skippedNoCoords = 0;
let skippedNoData = 0;
let skippedBadProjection = 0;
let outOfBounds = 0;

for (const [leaid, dist] of Object.entries(curriculum.districts)) {
  // Must have at least one curriculum assignment
  if (!dist.k5 && !dist.g68) {
    skippedNoData++;
    continue;
  }

  const centroid = centroids[leaid];
  if (!centroid || centroid.lat == null || centroid.lng == null) {
    skippedNoCoords++;
    continue;
  }

  const projected = projectionFromLatLon(centroid.lng, centroid.lat);
  if (!projected) {
    skippedBadProjection++;
    continue;
  }

  const [x, y] = projected;

  // Validate bounds (975x610 viewport with small tolerance)
  if (x < -20 || x > 995 || y < -20 || y > 630) {
    outOfBounds++;
    // Still include but log warning
    console.warn(`  Out of bounds: ${dist.name} (${dist.state}) at [${x.toFixed(1)}, ${y.toFixed(1)}] from lat=${centroid.lat}, lng=${centroid.lng}`);
  }

  const record = {
    n: dist.name,
    s: dist.state,
    x: Math.round(x * 10) / 10,
    y: Math.round(y * 10) / 10,
    sc: dist.schools || centroid.schools || 1,
    id: leaid
  };

  if (dist.k5) {
    record.k5 = k5Index[dist.k5];
    record.k5v = dist.k5_status === 'verified' ? 1 : 0;
  }
  if (dist.g68) {
    record.g68 = g68Index[dist.g68];
    record.g68v = dist.g68_status === 'verified' ? 1 : 0;
  }

  districts.push(record);
}

// ---- Per-state alignment (fixes projection mismatch with topology) ----
console.log('\nAligning districts to state boundaries...');
const topoRaw = JSON.parse(fs.readFileSync(topoPath, 'utf-8'));
const stateFeatures = topoDecode(topoRaw)('states');

// Build FIPS → feature lookup
const featureByFips = {};
for (const f of stateFeatures) {
  featureByFips[f.id] = f;
}

// Group districts by state
const byState = {};
for (const d of districts) {
  if (!byState[d.s]) byState[d.s] = [];
  byState[d.s].push(d);
}

let alignedStates = 0;
let skippedStates = 0;

for (const [stateCode, stateDists] of Object.entries(byState)) {
  const fips = FIPS[stateCode];
  const feature = fips ? featureByFips[fips] : null;
  if (!feature) {
    console.warn(`  No topology for state ${stateCode}, skipping alignment`);
    skippedStates++;
    continue;
  }

  const bb = getBBox(feature);
  if (bb.w < 1 || bb.h < 1) {
    skippedStates++;
    continue;
  }

  // Compute data points bounding box for this state
  let dx0=Infinity, dy0=Infinity, dx1=-Infinity, dy1=-Infinity;
  for (const d of stateDists) {
    if (d.x < dx0) dx0 = d.x;
    if (d.y < dy0) dy0 = d.y;
    if (d.x > dx1) dx1 = d.x;
    if (d.y > dy1) dy1 = d.y;
  }
  const dw = dx1 - dx0, dh = dy1 - dy0;

  // Skip states with only 1 point or zero-size bbox (can't compute scale)
  if (dw < 0.1 && dh < 0.1) {
    // Single point or very tight cluster: just center it in the state bbox
    const cx = (bb.x0 + bb.x1) / 2;
    const cy = (bb.y0 + bb.y1) / 2;
    for (const d of stateDists) {
      d.x = Math.round(cx * 10) / 10;
      d.y = Math.round(cy * 10) / 10;
    }
    alignedStates++;
    continue;
  }

  // Uniform scale to fit data within state bbox (with 4% padding)
  const pad = 0.04;
  const availW = bb.w * (1 - 2 * pad);
  const availH = bb.h * (1 - 2 * pad);
  const scale = Math.min(availW / dw, availH / dh);
  const scaledW = dw * scale, scaledH = dh * scale;
  const ox = bb.x0 + bb.w * pad + (availW - scaledW) / 2;
  const oy = bb.y0 + bb.h * pad + (availH - scaledH) / 2;

  for (const d of stateDists) {
    d.x = Math.round((ox + (d.x - dx0) * scale) * 10) / 10;
    d.y = Math.round((oy + (d.y - dy0) * scale) * 10) / 10;
  }
  alignedStates++;
}

console.log(`  Aligned: ${alignedStates} states, Skipped: ${skippedStates} states`);

// Sort districts by school count descending (for zoom-tier label display)
districts.sort((a, b) => (b.sc || 0) - (a.sc || 0));

const output = {
  generated: new Date().toISOString().slice(0, 10),
  total: districts.length,
  curricula_k5,
  curricula_g68,
  districts
};

// ---- Write output ----
const json = JSON.stringify(output);
fs.writeFileSync(outputPath, json);
const sizeMB = (Buffer.byteLength(json) / 1024 / 1024).toFixed(2);

console.log('\n--- Build Summary ---');
console.log(`Districts with curriculum data: ${districts.length}`);
console.log(`Skipped (no coords): ${skippedNoCoords}`);
console.log(`Skipped (no curriculum): ${skippedNoData}`);
console.log(`Skipped (projection failed): ${skippedBadProjection}`);
console.log(`Out of bounds warnings: ${outOfBounds}`);
console.log(`K-5 curricula: ${curricula_k5.length}`);
console.log(`6-8 curricula: ${curricula_g68.length}`);
console.log(`Output: ${outputPath} (${sizeMB} MB)`);

// ---- Spot-check known districts ----
console.log('\n--- Spot Checks ---');
const spotChecks = [
  { leaid: '0200180', name: 'Anchorage' },
  { leaid: '4823640', name: 'Houston' },
  { leaid: '2505160', name: 'Boston' },
  { leaid: '3620580', name: 'New York City' },
];
for (const sc of spotChecks) {
  const d = districts.find(d => d.id === sc.leaid);
  if (d) {
    const k5Name = d.k5 != null ? curricula_k5[d.k5] : 'none';
    const g68Name = d.g68 != null ? curricula_g68[d.g68] : 'none';
    console.log(`  ${sc.name}: [${d.x}, ${d.y}] K5=${k5Name} G68=${g68Name}`);
  } else {
    console.log(`  ${sc.name} (${sc.leaid}): not found in output`);
  }
}
