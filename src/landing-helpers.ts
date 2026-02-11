/**
 * Pure helper functions for the landing page (map-v3.html).
 * Extracted so they can be unit-tested independently.
 */

const ADMIN_PATTERN = /^(township|county|borough|parish|district|division|precinct|unincorporated)\b/i;

/**
 * Build the canonical explore page URL.
 * Single source of truth for the explore link href.
 */
export function buildExploreHref(
  state: string | null,
  lat?: number,
  lon?: number,
  city?: string
): string {
  const params = new URLSearchParams();
  if (state) params.set("state", state);
  if (lat != null && lon != null) {
    params.set("lat", String(lat));
    params.set("lon", String(lon));
  }
  if (city) params.set("city", city);
  const qs = params.toString();
  return qs ? `/explore.html?${qs}` : "/explore.html";
}

export interface BigDataCloudResponse {
  city?: string;
  locality?: string;
  principalSubdivision?: string;
  principalSubdivisionCode?: string;
  localityInfo?: {
    administrative?: Array<{ name: string; order?: number; description?: string }>;
    informative?: Array<{ name: string; order?: number; description?: string }>;
  };
}

/**
 * Extract a human-friendly location label from a BigDataCloud reverse-geocode response.
 *
 * Priority (per Codex review):
 * 1. localityInfo.informative entries with order >= 4 that are NOT admin names
 * 2. city (if populated)
 * 3. locality (only if it doesn't match admin patterns)
 * 4. principalSubdivision (state name) as last resort
 */
export function extractLocationLabel(geo: BigDataCloudResponse): string {
  // 1. Check informative entries for human place names
  if (geo.localityInfo?.informative) {
    const candidates = geo.localityInfo.informative
      .filter(
        (e) =>
          e.order != null &&
          e.order >= 4 &&
          e.name &&
          !ADMIN_PATTERN.test(e.name.trim())
      )
      .sort((a, b) => (b.order ?? 0) - (a.order ?? 0));
    if (candidates.length > 0) {
      return candidates[0].name;
    }
  }

  // 2. Fall back to city if populated
  if (geo.city && geo.city.trim()) {
    return geo.city.trim();
  }

  // 3. Fall back to locality if it doesn't match admin patterns
  if (geo.locality && geo.locality.trim() && !ADMIN_PATTERN.test(geo.locality.trim())) {
    return geo.locality.trim();
  }

  // 4. Last resort: state name
  if (geo.principalSubdivision && geo.principalSubdivision.trim()) {
    return geo.principalSubdivision.trim();
  }

  return "";
}
