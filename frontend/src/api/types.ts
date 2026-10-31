import type { components } from "./schema";

type S = components["schemas"];

export type TokenResponse = S["TokenResponse"];
export type Role = TokenResponse["role"];
export type SummaryResponse = S["SummaryResponse"];
export type TimeseriesResponse = S["TimeseriesResponse"];
export type TimeseriesPoint = S["TimeseriesPoint"];
export type TopProductsResponse = S["TopProductsResponse"];
export type TopProduct = S["TopProduct"];
export type BreakdownResponse = S["BreakdownResponse"];
export type DimensionBreakdown = S["DimensionBreakdown"];
export type StoreOut = S["StoreOut"];
export type StoreCreate = S["StoreCreate"];
export type StoreCreated = S["StoreCreated"];
export type ProductOut = S["ProductOut"];
export type ProductCreate = S["ProductCreate"];
export type StorePage = S["Page_StoreOut_"];
export type ProductPage = S["Page_ProductOut_"];
export type ExportCreate = S["ExportCreate"];
export type ExportFilters = S["ExportFilters"];
export type ExportJob = S["ExportJobResponse"];
export type ExportStatus = ExportJob["status"];
export type ExportFormat = ExportCreate["format"];
export type DownloadResponse = S["DownloadResponse"];
export type Meta = S["Meta"];

export type Channel = NonNullable<ExportFilters["channel"]>;
export type Granularity = "hour" | "day";
export type Metric = "revenue" | "units";

export const CHANNELS: readonly Channel[] = ["pos", "online", "mobile"];

/** Query parameters shared by the analytics endpoints. */
export interface AnalyticsParams {
  from: string;
  to: string;
  store_id?: string;
  region?: string;
  channel?: Channel;
}

/** Roles that may call everything except admin-only writes. */
export function canAnalyze(role: Role | undefined | null): boolean {
  return role === "analyst" || role === "admin";
}
