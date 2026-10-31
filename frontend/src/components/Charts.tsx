import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { BreakdownResponse, TimeseriesResponse } from "../api/types";
import { formatHour, formatHourShort, formatInt, formatMoney, PALETTE } from "../lib/format";
import ChartCard from "./ChartCard";
import WidgetState from "./WidgetState";

interface QueryProps<T> {
  data?: T;
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
}

const CHART_HEIGHT = 260;
const AXIS = { fontSize: 12, fill: "#333" };

function seriesPoints(data?: TimeseriesResponse) {
  return (data?.points ?? []).map((p) => ({ bucket: p.bucket, label: formatHourShort(p.bucket), value: Number(p.value) }));
}

/** Live hourly revenue. Animation is off so frequent live updates stay smooth. */
export function RevenueLineChart({ data, isLoading, error, onRetry }: QueryProps<TimeseriesResponse>) {
  const pts = seriesPoints(data);
  const currency = data?.meta.currency;
  const total = pts.reduce((s, p) => s + p.value, 0);
  const peak = pts.reduce((m, p) => (p.value > m.value ? p : m), pts[0] ?? { value: 0, bucket: "", label: "" });
  const summary = pts.length
    ? `Hourly revenue for the last ${pts.length} hours: total ${formatMoney(total, currency)}, peak ${formatMoney(
        peak.value,
        currency,
      )} at ${peak.label}, latest ${formatMoney(pts[pts.length - 1].value, currency)}.`
    : "No hourly revenue data.";
  return (
    <section aria-labelledby="rev-heading" className="widget">
      <h2 id="rev-heading">Hourly revenue (last 24 hours, live)</h2>
      <WidgetState
        label="hourly revenue"
        isLoading={isLoading}
        error={error}
        isEmpty={pts.length === 0}
        emptyMessage="No sales in the last 24 hours."
        onRetry={onRetry}
      >
        <ChartCard
          title="Hourly revenue"
          summary={summary}
          columns={["Hour", "Revenue"]}
          rows={(data?.points ?? []).map((p) => [formatHour(p.bucket), formatMoney(p.value, currency)])}
        >
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <LineChart data={pts} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              <CartesianGrid stroke="#d0d5dd" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={AXIS} />
              <YAxis tick={AXIS} width={70} tickFormatter={(v: number) => formatInt(v)} />
              <Tooltip formatter={(v) => formatMoney(Number(v), currency)} />
              <Line
                type="monotone"
                dataKey="value"
                name="Revenue"
                stroke={PALETTE[0]}
                strokeWidth={2.5}
                dot={{ r: 3 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>
      </WidgetState>
    </section>
  );
}

export function UnitsBarChart({ data, isLoading, error, onRetry }: QueryProps<TimeseriesResponse>) {
  const pts = seriesPoints(data);
  const total = pts.reduce((s, p) => s + p.value, 0);
  const summary = pts.length
    ? `Units sold per hour for the last ${pts.length} hours: ${formatInt(total)} in total.`
    : "No hourly unit data.";
  return (
    <section aria-labelledby="units-heading" className="widget">
      <h2 id="units-heading">Units per hour (last 24 hours, live)</h2>
      <WidgetState
        label="hourly units"
        isLoading={isLoading}
        error={error}
        isEmpty={pts.length === 0}
        emptyMessage="No units sold in the last 24 hours."
        onRetry={onRetry}
      >
        <ChartCard
          title="Units per hour"
          summary={summary}
          columns={["Hour", "Units"]}
          rows={(data?.points ?? []).map((p) => [formatHour(p.bucket), formatInt(p.value)])}
        >
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <BarChart data={pts} margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              <CartesianGrid stroke="#d0d5dd" strokeDasharray="3 3" />
              <XAxis dataKey="label" tick={AXIS} />
              <YAxis tick={AXIS} width={50} allowDecimals={false} />
              <Tooltip formatter={(v) => formatInt(Number(v))} />
              <Bar dataKey="value" name="Units" fill={PALETTE[2]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </WidgetState>
    </section>
  );
}

function breakdownRows(data?: BreakdownResponse) {
  return (data?.items ?? []).map((i) => ({ ...i, value: Number(i.revenue) }));
}

export function RegionChart({ data, isLoading, error, onRetry }: QueryProps<BreakdownResponse>) {
  const rows = breakdownRows(data);
  const currency = data?.meta.currency;
  const top = rows.reduce((m, r) => (r.value > m.value ? r : m), rows[0] ?? { label: "", value: 0 });
  const summary = rows.length
    ? `Revenue across ${rows.length} regions. Highest: ${top.label} with ${formatMoney(top.value, currency)}.`
    : "No regional data.";
  return (
    <section aria-labelledby="region-heading" className="widget">
      <h2 id="region-heading">Revenue by region</h2>
      <WidgetState
        label="regional revenue"
        isLoading={isLoading}
        error={error}
        isEmpty={rows.length === 0}
        emptyMessage="No regional sales in the selected period."
        onRetry={onRetry}
      >
        <ChartCard
          title="Revenue by region"
          summary={summary}
          columns={["Region", "Revenue", "Units", "Transactions"]}
          rows={rows.map((r) => [r.label, formatMoney(r.revenue, currency), formatInt(r.units), formatInt(r.transactions)])}
        >
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <BarChart data={rows} layout="vertical" margin={{ top: 8, right: 16, bottom: 0, left: 8 }}>
              <CartesianGrid stroke="#d0d5dd" strokeDasharray="3 3" />
              <XAxis type="number" tick={AXIS} tickFormatter={(v: number) => formatInt(v)} />
              <YAxis type="category" dataKey="label" tick={AXIS} width={90} />
              <Tooltip formatter={(v) => formatMoney(Number(v), currency)} />
              <Bar dataKey="value" name="Revenue" fill={PALETTE[0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </WidgetState>
    </section>
  );
}

export function ChannelChart({ data, isLoading, error, onRetry }: QueryProps<BreakdownResponse>) {
  const rows = breakdownRows(data);
  const currency = data?.meta.currency;
  const total = rows.reduce((s, r) => s + r.value, 0);
  const pct = (v: number) => (total > 0 ? `${((v / total) * 100).toFixed(1)}%` : "0%");
  const summary = rows.length
    ? `Revenue split by channel: ${rows.map((r) => `${r.label} ${pct(r.value)}`).join(", ")}.`
    : "No channel data.";
  return (
    <section aria-labelledby="channel-heading" className="widget">
      <h2 id="channel-heading">Revenue by channel</h2>
      <WidgetState
        label="channel revenue"
        isLoading={isLoading}
        error={error}
        isEmpty={rows.length === 0 || total === 0}
        emptyMessage="No channel sales in the selected period."
        onRetry={onRetry}
      >
        <ChartCard
          title="Revenue by channel"
          summary={summary}
          columns={["Channel", "Revenue", "Share", "Units", "Transactions"]}
          rows={rows.map((r) => [r.label, formatMoney(r.revenue, currency), pct(r.value), formatInt(r.units), formatInt(r.transactions)])}
        >
          <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
            <PieChart>
              <Pie
                data={rows}
                dataKey="value"
                nameKey="label"
                innerRadius={50}
                outerRadius={90}
                stroke="#fff"
                strokeWidth={2}
                isAnimationActive={false}
                label={(e: { name?: string; value?: number }) => `${e.name ?? ""} ${pct(e.value ?? 0)}`}
              >
                {rows.map((r, i) => (
                  <Cell key={r.label} fill={PALETTE[i % PALETTE.length]} />
                ))}
              </Pie>
              <Tooltip formatter={(v) => formatMoney(Number(v), currency)} />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>
      </WidgetState>
    </section>
  );
}
