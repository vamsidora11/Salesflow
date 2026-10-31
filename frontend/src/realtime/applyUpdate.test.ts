import { describe, expect, it } from "vitest";
import type { SummaryResponse, TimeseriesPoint } from "../api/types";
import { addDecimal } from "../lib/decimal";
import { applyUpdateToSeries, applyUpdateToSummary, MAX_BUCKETS, mergeUpdates, type LiveUpdate } from "./applyUpdate";

const NOW = new Date("2026-10-30T14:30:00Z");

const upd = (over: Partial<LiveUpdate> = {}): LiveUpdate => ({
  type: "update",
  seq: 1,
  bucket: "2026-10-30T14:00:00+00:00",
  store_id: "s1",
  region: "Europe",
  revenue: "1523.40",
  units: 88,
  delta_revenue: "44.98",
  delta_units: 3,
  ...over,
});

describe("addDecimal", () => {
  it("is exact", () => {
    expect(addDecimal("0.10", "0.20")).toBe("0.30");
    expect(addDecimal("1523.40", "44.98")).toBe("1568.38");
    expect(addDecimal("10", "3", 0)).toBe("13");
    expect(addDecimal("5.00", "-7.25")).toBe("-2.25");
  });
});

describe("applyUpdateToSeries", () => {
  const pts: TimeseriesPoint[] = [
    { bucket: "2026-10-30T13:00:00Z", value: "10.00" },
    { bucket: "2026-10-30T14:00:00Z", value: "100.00" },
  ];

  it("adds the revenue delta to an existing bucket (timestamp formats differ)", () => {
    const out = applyUpdateToSeries(pts, upd(), "revenue", NOW);
    expect(out[1].value).toBe("144.98");
    expect(out[0].value).toBe("10.00");
    expect(pts[1].value).toBe("100.00"); // input not mutated
  });

  it("adds the units delta for the units metric", () => {
    const units = [{ bucket: "2026-10-30T14:00:00Z", value: "7" }];
    expect(applyUpdateToSeries(units, upd(), "units", NOW)[0].value).toBe("10");
  });

  it("creates a missing bucket inside the last 24h, kept sorted", () => {
    const out = applyUpdateToSeries([pts[1]], upd({ bucket: "2026-10-30T10:00:00+00:00" }), "revenue", NOW);
    expect(out).toHaveLength(2);
    expect(out[0]).toEqual({ bucket: "2026-10-30T10:00:00+00:00", value: "44.98" });
    expect(out[1].bucket).toBe(pts[1].bucket);
  });

  it("creates the current bucket on an empty series", () => {
    const out = applyUpdateToSeries([], upd(), "revenue", NOW);
    expect(out).toEqual([{ bucket: "2026-10-30T14:00:00+00:00", value: "44.98" }]);
  });

  it("ignores buckets outside the window", () => {
    const old = applyUpdateToSeries(pts, upd({ bucket: "2026-10-29T10:00:00+00:00" }), "revenue", NOW);
    expect(old).toEqual(pts);
    const future = applyUpdateToSeries(pts, upd({ bucket: "2026-10-30T20:00:00+00:00" }), "revenue", NOW);
    expect(future).toEqual(pts);
  });

  it("keeps the array bounded", () => {
    const many: TimeseriesPoint[] = Array.from({ length: MAX_BUCKETS + 10 }, (_, i) => ({
      bucket: new Date(Date.UTC(2026, 9, 1, i)).toISOString(),
      value: "1.00",
    }));
    const out = applyUpdateToSeries(many, upd(), "revenue", NOW);
    expect(out.length).toBeLessThanOrEqual(MAX_BUCKETS);
    expect(Date.parse(out[out.length - 1].bucket)).toBe(Date.parse(upd().bucket));
  });
});

describe("applyUpdateToSummary", () => {
  const summary: SummaryResponse = {
    revenue: "1000.00",
    units: 50,
    transactions: 20,
    average_order_value: "50.00",
    meta: { generated_at: "2026-10-30T14:00:00Z", data_freshness_seconds: 3, currency: "USD" },
  };

  it("adds revenue and units, leaves transactions/AOV/meta alone", () => {
    const out = applyUpdateToSummary(summary, upd());
    expect(out.revenue).toBe("1044.98");
    expect(out.units).toBe(53);
    expect(out.transactions).toBe(20);
    expect(out.average_order_value).toBe("50.00");
    expect(out.meta).toEqual(summary.meta);
    expect(summary.revenue).toBe("1000.00");
  });
});

describe("mergeUpdates", () => {
  it("keeps latest totals, sums deltas, highest seq", () => {
    const m = mergeUpdates(upd({ seq: 5 }), upd({ seq: 9, revenue: "1600.00", units: 90, delta_revenue: "1.02", delta_units: 2 }));
    expect(m).toMatchObject({ seq: 9, revenue: "1600.00", units: 90, delta_revenue: "46.00", delta_units: 5 });
  });
});
