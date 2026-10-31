import { addDecimal } from "../lib/decimal";
import type { Metric, SummaryResponse, TimeseriesPoint } from "../api/types";

/** WebSocket `update` message (docs/websocket.md). */
export interface LiveUpdate {
  type: "update";
  seq: number;
  bucket: string;
  store_id: string;
  region: string;
  revenue: string;
  units: number;
  delta_revenue: string;
  delta_units: number;
}

export const HOUR_MS = 3_600_000;
export const WINDOW_HOURS = 24;
export const MAX_BUCKETS = 48;

export function isLiveUpdate(m: unknown): m is LiveUpdate {
  if (!m || typeof m !== "object") return false;
  const o = m as Record<string, unknown>;
  return (
    o.type === "update" &&
    typeof o.bucket === "string" &&
    !Number.isNaN(Date.parse(o.bucket)) &&
    typeof o.store_id === "string" &&
    typeof o.delta_revenue === "string" &&
    typeof o.delta_units === "number"
  );
}

/** Coalesce two updates for the same (bucket, store): latest totals, summed deltas. */
export function mergeUpdates(prev: LiveUpdate, next: LiveUpdate): LiveUpdate {
  return {
    ...next,
    seq: Math.max(prev.seq, next.seq),
    delta_revenue: addDecimal(prev.delta_revenue, next.delta_revenue),
    delta_units: prev.delta_units + next.delta_units,
  };
}

export const updateKey = (u: LiveUpdate) => `${Date.parse(u.bucket)}|${u.store_id}`;

export interface LiveFilters {
  store_id?: string;
  region?: string;
  channel?: string;
}

/** Whether an update belongs to the filtered view (channel is handled separately). */
export function updateMatchesFilters(u: LiveUpdate, f: LiveFilters): boolean {
  if (f.store_id && u.store_id !== f.store_id) return false;
  if (f.region && u.region !== f.region) return false;
  return true;
}

function deltaFor(u: LiveUpdate, metric: Metric): string {
  return metric === "revenue" ? u.delta_revenue : String(u.delta_units);
}

/**
 * Apply an update's delta to a cached hourly series. Existing buckets are adjusted;
 * a missing bucket is created only when it lies inside the last 24 hours (and not in
 * the future). The result is sorted and bounded to MAX_BUCKETS.
 */
export function applyUpdateToSeries(
  points: readonly TimeseriesPoint[],
  update: LiveUpdate,
  metric: Metric,
  now: Date = new Date(),
): TimeseriesPoint[] {
  const t = Date.parse(update.bucket);
  const delta = deltaFor(update, metric);
  const idx = points.findIndex((p) => Date.parse(p.bucket) === t);
  let next: TimeseriesPoint[];
  if (idx >= 0) {
    next = points.map((p, i) => (i === idx ? { ...p, value: addDecimal(p.value, delta, metric === "units" ? 0 : 2) } : p));
  } else {
    const currentHour = Math.floor(now.getTime() / HOUR_MS) * HOUR_MS;
    const oldest = currentHour - (WINDOW_HOURS - 1) * HOUR_MS;
    if (t < oldest || t > currentHour) return points.slice(-MAX_BUCKETS);
    next = [...points, { bucket: update.bucket, value: addDecimal("0", delta, metric === "units" ? 0 : 2) }];
    next.sort((a, b) => Date.parse(a.bucket) - Date.parse(b.bucket));
  }
  return next.slice(-MAX_BUCKETS);
}

/** Apply revenue/units deltas to a cached summary. Transactions/AOV wait for a refetch. */
export function applyUpdateToSummary(summary: SummaryResponse, update: LiveUpdate): SummaryResponse {
  return {
    ...summary,
    revenue: addDecimal(summary.revenue, update.delta_revenue),
    units: summary.units + update.delta_units,
  };
}
