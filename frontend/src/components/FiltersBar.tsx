import type { StoreOut } from "../api/types";
import { CHANNELS, type Channel } from "../api/types";
import { PRESET_LABELS, type DashboardFilters, type RangePreset } from "../lib/filters";

interface Props {
  filters: DashboardFilters;
  onChange: (next: DashboardFilters) => void;
  stores: StoreOut[];
  storesLoading?: boolean;
  /** Viewers only get the date range: they cannot list stores or filter other widgets. */
  dateOnly?: boolean;
}

export default function FiltersBar({ filters, onChange, stores, storesLoading, dateOnly }: Props) {
  const regions = Array.from(new Set(stores.map((s) => s.region))).sort();
  const set = (patch: Partial<DashboardFilters>) => onChange({ ...filters, ...patch });
  const rangeInvalid =
    filters.preset === "custom" && (!filters.customFrom || !filters.customTo || filters.customFrom > filters.customTo);

  return (
    <form className="filters" aria-label="Dashboard filters" onSubmit={(e) => e.preventDefault()}>
      <div className="field">
        <label htmlFor="f-range">Date range</label>
        <select id="f-range" value={filters.preset} onChange={(e) => set({ preset: e.target.value as RangePreset })}>
          {(Object.keys(PRESET_LABELS) as (keyof typeof PRESET_LABELS)[]).map((k) => (
            <option key={k} value={k}>
              {PRESET_LABELS[k]}
            </option>
          ))}
          <option value="custom">Custom range</option>
        </select>
      </div>
      {filters.preset === "custom" && (
        <>
          <div className="field">
            <label htmlFor="f-from">From (UTC)</label>
            <input id="f-from" type="date" value={filters.customFrom} onChange={(e) => set({ customFrom: e.target.value })} />
          </div>
          <div className="field">
            <label htmlFor="f-to">To (UTC, inclusive)</label>
            <input id="f-to" type="date" value={filters.customTo} onChange={(e) => set({ customTo: e.target.value })} />
          </div>
        </>
      )}
      {!dateOnly && (
        <>
          <div className="field">
            <label htmlFor="f-store">Store</label>
            <select id="f-store" value={filters.store_id} onChange={(e) => set({ store_id: e.target.value })}>
              <option value="">{storesLoading ? "Loading stores…" : "All stores"}</option>
              {stores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-region">Region</label>
            <select id="f-region" value={filters.region} onChange={(e) => set({ region: e.target.value })}>
              <option value="">All regions</option>
              {regions.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="f-channel">Channel</label>
            <select id="f-channel" value={filters.channel} onChange={(e) => set({ channel: e.target.value as Channel | "" })}>
              <option value="">All channels</option>
              {CHANNELS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
        </>
      )}
      {rangeInvalid && (
        <p className="field-error" role="alert">
          Choose a start date on or before the end date.
        </p>
      )}
    </form>
  );
}
