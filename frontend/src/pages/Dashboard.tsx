import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getByChannel, getByRegion, getSummary, getTimeseries, getTopProducts, listStores } from "../api/endpoints";
import type { StoreOut } from "../api/types";
import { canAnalyze } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ChannelChart, RegionChart, RevenueLineChart, UnitsBarChart } from "../components/Charts";
import FiltersBar from "../components/FiltersBar";
import KpiCards from "../components/KpiCards";
import LiveIndicator from "../components/LiveIndicator";
import TopProductsTable from "../components/TopProductsTable";
import {
  defaultFilters,
  dimensionParams,
  hourlyWindow,
  isLiveRange,
  isRangeValid,
  rangeKey,
  resolveRange,
  type DashboardFilters,
} from "../lib/filters";
import { keys } from "../queryKeys";
import { useLiveDashboard } from "../realtime/useLiveDashboard";

/** Reconciling refetch: any drift from live deltas self-corrects within a minute. */
export const RECONCILE_MS = 60_000;

async function fetchAllStores(): Promise<StoreOut[]> {
  const all: StoreOut[] = [];
  let cursor: string | null | undefined;
  for (let i = 0; i < 10; i++) {
    const page = await listStores({ limit: 200, cursor });
    all.push(...page.items);
    cursor = page.next_cursor;
    if (!cursor) break;
  }
  return all;
}

export default function Dashboard() {
  const { role, token } = useAuth();
  const analyst = canAnalyze(role);
  const [filters, setFilters] = useState<DashboardFilters>(defaultFilters);

  const valid = isRangeValid(filters);
  const rk = rangeKey(filters);
  // Viewers cannot use dimension filters; ignore them defensively.
  const dims: ReturnType<typeof dimensionParams> = analyst
    ? dimensionParams(filters)
    : { store_id: undefined, region: undefined, channel: undefined };
  const { store_id, region, channel } = dims;
  const live = { store_id, region, channel };

  const storesQ = useQuery({ queryKey: keys.allStores, queryFn: fetchAllStores, enabled: analyst });

  const summaryKey = keys.summary(rk, live);
  const summaryQ = useQuery({
    queryKey: summaryKey,
    queryFn: ({ signal }) => getSummary({ ...resolveRange(filters), ...dims }, signal),
    enabled: valid,
    refetchInterval: RECONCILE_MS,
  });

  const hourlyRevenueQ = useQuery({
    queryKey: keys.hourly("revenue", live),
    queryFn: ({ signal }) =>
      getTimeseries({ ...hourlyWindow(), ...dims, granularity: "hour", metric: "revenue" }, signal),
    enabled: analyst,
    refetchInterval: RECONCILE_MS,
  });
  const hourlyUnitsQ = useQuery({
    queryKey: keys.hourly("units", live),
    queryFn: ({ signal }) => getTimeseries({ ...hourlyWindow(), ...dims, granularity: "hour", metric: "units" }, signal),
    enabled: analyst,
    refetchInterval: RECONCILE_MS,
  });
  const topQ = useQuery({
    queryKey: keys.topProducts(rk, live),
    queryFn: ({ signal }) => getTopProducts({ ...resolveRange(filters), ...dims, limit: 10 }, signal),
    enabled: analyst && valid,
    refetchInterval: RECONCILE_MS,
  });
  const regionQ = useQuery({
    queryKey: keys.byRegion(rk, { store_id, channel }),
    queryFn: ({ signal }) => getByRegion({ ...resolveRange(filters), store_id, channel }, signal),
    enabled: analyst && valid,
    refetchInterval: RECONCILE_MS,
  });
  const channelQ = useQuery({
    queryKey: keys.byChannel(rk, { store_id, region }),
    queryFn: ({ signal }) => getByChannel({ ...resolveRange(filters), store_id, region }, signal),
    enabled: analyst && valid,
    refetchInterval: RECONCILE_MS,
  });

  const liveState = useLiveDashboard({
    token,
    enabled: analyst,
    filters: live,
    summaryKey: isLiveRange(filters) ? summaryKey : null,
  });

  return (
    <div className="dashboard">
      <div className="page-head">
        <h1>Dashboard</h1>
        {analyst ? (
          <LiveIndicator status={liveState.status} lastUpdatedAt={liveState.lastUpdatedAt} />
        ) : (
          <p className="muted">Live updates are available to analysts and admins.</p>
        )}
      </div>

      <FiltersBar
        filters={filters}
        onChange={setFilters}
        stores={storesQ.data ?? []}
        storesLoading={analyst && storesQ.isLoading}
        dateOnly={!analyst}
      />
      {analyst && storesQ.isError && (
        <p className="state state-error" role="alert">
          Could not load the store list, so store and region filters are unavailable.
        </p>
      )}

      <KpiCards
        data={summaryQ.data}
        isLoading={valid && summaryQ.isLoading}
        error={summaryQ.error}
        onRetry={() => void summaryQ.refetch()}
      />

      {analyst ? (
        <>
          <div className="grid-2">
            <RevenueLineChart
              data={hourlyRevenueQ.data}
              isLoading={hourlyRevenueQ.isLoading}
              error={hourlyRevenueQ.error}
              onRetry={() => void hourlyRevenueQ.refetch()}
            />
            <UnitsBarChart
              data={hourlyUnitsQ.data}
              isLoading={hourlyUnitsQ.isLoading}
              error={hourlyUnitsQ.error}
              onRetry={() => void hourlyUnitsQ.refetch()}
            />
          </div>
          <TopProductsTable
            data={topQ.data}
            isLoading={valid && topQ.isLoading}
            error={topQ.error}
            onRetry={() => void topQ.refetch()}
          />
          <div className="grid-2">
            <RegionChart
              data={regionQ.data}
              isLoading={valid && regionQ.isLoading}
              error={regionQ.error}
              onRetry={() => void regionQ.refetch()}
            />
            <ChannelChart
              data={channelQ.data}
              isLoading={valid && channelQ.isLoading}
              error={channelQ.error}
              onRetry={() => void channelQ.refetch()}
            />
          </div>
        </>
      ) : (
        <section className="widget" aria-labelledby="restricted-heading">
          <h2 id="restricted-heading">Charts and breakdowns</h2>
          <p className="state state-empty">
            Requires analyst role: charts, top products and breakdowns are hidden for viewers. Ask an admin for access.
          </p>
        </section>
      )}
    </div>
  );
}
