import type { Metric } from "./api/types";

/** Every live/dashboard query lives under this prefix so one call can invalidate them all. */
export const DASHBOARD = "dashboard";

export interface FilterValues {
  store_id?: string;
  region?: string;
  channel?: string;
}

const norm = (f: FilterValues) => ({
  store_id: f.store_id || null,
  region: f.region || null,
  channel: f.channel || null,
});

/** `range` is a stable label (a preset name or `custom:from:to`), never "now". */
export const keys = {
  summary: (range: string, f: FilterValues) => [DASHBOARD, "summary", range, norm(f)] as const,
  hourly: (metric: Metric, f: FilterValues) => [DASHBOARD, "timeseries", "hour", metric, norm(f)] as const,
  topProducts: (range: string, f: FilterValues) => [DASHBOARD, "top-products", range, norm(f)] as const,
  byRegion: (range: string, f: FilterValues) => [DASHBOARD, "by-region", range, norm(f)] as const,
  byChannel: (range: string, f: FilterValues) => [DASHBOARD, "by-channel", range, norm(f)] as const,
  allStores: ["stores", "all"] as const,
};
