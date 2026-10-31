import type { AnalyticsParams, Channel } from "../api/types";

export type RangePreset = "24h" | "7d" | "30d" | "custom";

export interface DashboardFilters {
  preset: RangePreset;
  /** `YYYY-MM-DD` (UTC dates) used when preset is "custom". */
  customFrom: string;
  customTo: string;
  store_id: string;
  region: string;
  channel: Channel | "";
}

export const PRESET_LABELS: Record<Exclude<RangePreset, "custom">, string> = {
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
};

const PRESET_MS: Record<Exclude<RangePreset, "custom">, number> = {
  "24h": 24 * 3_600_000,
  "7d": 7 * 24 * 3_600_000,
  "30d": 30 * 24 * 3_600_000,
};

export const defaultFilters = (): DashboardFilters => ({
  preset: "24h",
  customFrom: "",
  customTo: "",
  store_id: "",
  region: "",
  channel: "",
});

/** Stable label for query keys (never contains "now"). */
export function rangeKey(f: DashboardFilters): string {
  return f.preset === "custom" ? `custom:${f.customFrom}:${f.customTo}` : f.preset;
}

/** Presets (and only presets) end "now", so live deltas apply to them. */
export const isLiveRange = (f: DashboardFilters) => f.preset !== "custom";

export function isRangeValid(f: DashboardFilters): boolean {
  if (f.preset !== "custom") return true;
  return !!f.customFrom && !!f.customTo && f.customFrom <= f.customTo;
}

/** Resolve to concrete UTC ISO instants at fetch time. Custom `to` is inclusive of its day. */
export function resolveRange(f: DashboardFilters, now: Date = new Date()): { from: string; to: string } {
  if (f.preset === "custom") {
    const from = new Date(`${f.customFrom}T00:00:00Z`);
    const to = new Date(`${f.customTo}T00:00:00Z`);
    to.setUTCDate(to.getUTCDate() + 1);
    return { from: from.toISOString(), to: to.toISOString() };
  }
  const to = new Date(now.getTime());
  return { from: new Date(to.getTime() - PRESET_MS[f.preset]).toISOString(), to: to.toISOString() };
}

export function dimensionParams(f: DashboardFilters) {
  return {
    store_id: f.store_id || undefined,
    region: f.region || undefined,
    channel: f.channel || undefined,
  };
}

export function toAnalyticsParams(f: DashboardFilters, now?: Date): AnalyticsParams {
  return { ...resolveRange(f, now), ...dimensionParams(f) };
}

/** Window for the live hourly charts: the last 24 whole hours plus the current one. */
export function hourlyWindow(now: Date = new Date()): { from: string; to: string } {
  const hour = 3_600_000;
  const current = Math.floor(now.getTime() / hour) * hour;
  return { from: new Date(current - 23 * hour).toISOString(), to: new Date(current + hour).toISOString() };
}
