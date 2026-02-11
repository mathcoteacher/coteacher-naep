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
    administrative?: Array<{ name: string; order?: number; adminLevel?: number; description?: string }>;
    informative?: Array<{ name: string; order?: number; description?: string }>;
  };
}

/**
 * Extract a human-friendly location label from a BigDataCloud reverse-geocode response.
 *
 * Priority:
 * 1. locality (often the actual city, e.g. "Canal Winchester")
 * 2. city (sometimes set to township — filtered by admin pattern)
 * 3. administrative entries at city level (adminLevel >= 8)
 * 4. principalSubdivision (state name) as last resort
 *
 * NOTE: localityInfo.informative is NOT used — it contains FIPS codes,
 * postal codes, and timezone names, not city names.
 */
export function extractLocationLabel(geo: BigDataCloudResponse): string {
  // 1. locality — often the actual city name
  if (geo.locality?.trim() && !ADMIN_PATTERN.test(geo.locality.trim())) {
    return geo.locality.trim();
  }

  // 2. city — sometimes set to township/county, so filter admin names
  if (geo.city?.trim() && !ADMIN_PATTERN.test(geo.city.trim())) {
    return geo.city.trim();
  }

  // 3. administrative entries at city level (adminLevel >= 8)
  if (geo.localityInfo?.administrative) {
    const candidates = geo.localityInfo.administrative
      .filter(
        (e) =>
          e.adminLevel != null &&
          e.adminLevel >= 8 &&
          e.name &&
          !ADMIN_PATTERN.test(e.name.trim())
      )
      .sort((a, b) => (b.adminLevel ?? 0) - (a.adminLevel ?? 0));
    if (candidates.length > 0) {
      return candidates[0].name;
    }
  }

  // 4. Last resort: state name
  if (geo.principalSubdivision?.trim()) {
    return geo.principalSubdivision.trim();
  }

  return "";
}
