import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SummaryResponse, TimeseriesResponse } from "../api/types";
import { keys } from "../queryKeys";
import { FakeWebSocket, makeClient } from "../test/utils";
import { useLiveDashboard, type UseLiveDashboardOptions } from "./useLiveDashboard";

const meta = { generated_at: "2026-10-30T14:00:00Z", data_freshness_seconds: 1, currency: "USD" };
const TOKEN = "secret-jwt-token";

function setup(initial: Partial<UseLiveDashboardOptions> = {}) {
  const client = makeClient();
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const summaryKey = keys.summary("24h", {});
  client.setQueryData<TimeseriesResponse>(keys.hourly("revenue", {}), {
    granularity: "hour",
    metric: "revenue",
    meta,
    points: [{ bucket: "2026-10-30T14:00:00Z", value: "100.00" }],
  });
  client.setQueryData<TimeseriesResponse>(keys.hourly("units", {}), {
    granularity: "hour",
    metric: "units",
    meta,
    points: [{ bucket: "2026-10-30T14:00:00Z", value: "10" }],
  });
  client.setQueryData<SummaryResponse>(summaryKey, {
    revenue: "1000.00",
    units: 50,
    transactions: 5,
    average_order_value: "200.00",
    meta,
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(
    (props: Partial<UseLiveDashboardOptions>) =>
      useLiveDashboard({ token: TOKEN, filters: {}, summaryKey, onUnauthorized: vi.fn(), ...props }),
    { wrapper, initialProps: initial },
  );
  return { client, invalidate, summaryKey, ...hook };
}

const update = (over: Record<string, unknown> = {}) => ({
  type: "update",
  seq: 1,
  bucket: "2026-10-30T14:00:00+00:00",
  store_id: "s1",
  region: "Europe",
  revenue: "144.98",
  units: 13,
  delta_revenue: "44.98",
  delta_units: 3,
  ...over,
});

function connectAndAuth(i = 0) {
  const ws = FakeWebSocket.instances[i];
  act(() => {
    ws.open();
    ws.receive({ type: "auth.ok", seq: 0 });
  });
  return ws;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-30T14:30:00Z"));
  FakeWebSocket.reset();
  vi.stubGlobal("WebSocket", FakeWebSocket);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useLiveDashboard", () => {
  it("sends auth as the first message and keeps the token out of the URL", () => {
    setup();
    const ws = FakeWebSocket.instances[0];
    expect(ws.url).toMatch(/^wss?:\/\/.+\/api\/v1\/ws\/dashboard$/);
    expect(ws.url).not.toContain(TOKEN);
    expect(ws.url).not.toContain("token");
    act(() => ws.open());
    expect(ws.messages[0]).toEqual({ type: "auth", token: TOKEN });
  });

  it("subscribes after auth.ok and resubscribes when filters change", () => {
    const { result, rerender } = setup({ filters: { store_id: "s1" } });
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    expect(ws.messages).toHaveLength(1); // only auth so far
    act(() => ws.receive({ type: "auth.ok", seq: 3 }));
    expect(result.current.status).toBe("connected");
    expect(ws.messages[1]).toEqual({ type: "subscribe", store_id: "s1" });
    rerender({ filters: { region: "Europe" } });
    expect(ws.messages.slice(2)).toEqual([
      { type: "unsubscribe", store_id: "s1" },
      { type: "subscribe", region: "Europe" },
    ]);
  });

  it("applies a batched update to the query cache", () => {
    const { client, summaryKey } = setup();
    const ws = connectAndAuth();
    act(() => {
      ws.receive(update());
      ws.receive(update({ seq: 2, delta_revenue: "5.02", delta_units: 1 }));
    });
    // Not applied before the 500 ms flush.
    expect(client.getQueryData<TimeseriesResponse>(keys.hourly("revenue", {}))!.points[0].value).toBe("100.00");
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(client.getQueryData<TimeseriesResponse>(keys.hourly("revenue", {}))!.points[0].value).toBe("150.00");
    expect(client.getQueryData<TimeseriesResponse>(keys.hourly("units", {}))!.points[0].value).toBe("14");
    const s = client.getQueryData<SummaryResponse>(summaryKey)!;
    expect(s.revenue).toBe("1050.00");
    expect(s.units).toBe(54);
    expect(s.transactions).toBe(5);
  });

  it("throttle-invalidates instead of applying deltas when a channel filter is active", () => {
    const { client, invalidate } = setup({ filters: { channel: "pos" } });
    client.setQueryData(keys.hourly("revenue", { channel: "pos" }), {
      granularity: "hour",
      metric: "revenue",
      meta,
      points: [{ bucket: "2026-10-30T14:00:00Z", value: "1.00" }],
    });
    const ws = connectAndAuth();
    act(() => ws.receive(update()));
    act(() => {
      vi.advanceTimersByTime(500);
    });
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(client.getQueryData<TimeseriesResponse>(keys.hourly("revenue", { channel: "pos" }))!.points[0].value).toBe("1.00");
  });

  it("invalidates dashboard queries on resync_required", () => {
    const { invalidate } = setup();
    const ws = connectAndAuth();
    expect(invalidate).not.toHaveBeenCalled(); // first connect: no resync
    act(() => ws.receive({ type: "resync_required" }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dashboard"] });
  });

  it("reconnects with backoff after an unexpected close and invalidates afterwards", () => {
    const { result, invalidate } = setup();
    const ws = connectAndAuth();
    act(() => ws.serverClose(1006));
    expect(result.current.status).toBe("reconnecting");
    expect(FakeWebSocket.instances).toHaveLength(1);
    act(() => {
      vi.advanceTimersByTime(1000); // attempt 0: at most 1 s
    });
    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(invalidate).not.toHaveBeenCalled();
    const ws2 = connectAndAuth(1);
    expect(ws2.messages[0]).toEqual({ type: "auth", token: TOKEN });
    expect(result.current.status).toBe("connected");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["dashboard"] });
  });

  it("does not reconnect on 4401; it hands off to the unauthorised handler", () => {
    const onUnauthorized = vi.fn();
    const { result } = setup({ onUnauthorized });
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.serverClose(4401));
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(result.current.status).toBe("offline");
  });

  it("does not open a socket when disabled", () => {
    setup({ enabled: false });
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("cleans up on unmount", () => {
    const { unmount } = setup();
    const ws = connectAndAuth();
    act(() => ws.receive(update()));
    unmount();
    expect(ws.closeCalls).toBeGreaterThan(0);
    act(() => {
      vi.advanceTimersByTime(120_000);
    });
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
