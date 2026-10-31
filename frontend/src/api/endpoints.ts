import { request, requestBlob } from "./client";
import type {
  AnalyticsParams,
  BreakdownResponse,
  DownloadResponse,
  ExportCreate,
  ExportJob,
  Granularity,
  Metric,
  ProductCreate,
  ProductOut,
  ProductPage,
  StoreCreate,
  StoreCreated,
  StorePage,
  SummaryResponse,
  TimeseriesResponse,
  TokenResponse,
  TopProductsResponse,
} from "./types";

const A = "/api/v1/analytics";

export const login = (email: string, password: string) =>
  request<TokenResponse>("/api/v1/auth/login", { method: "POST", body: { email, password }, anonymous: true });

export const getSummary = (p: AnalyticsParams, signal?: AbortSignal) =>
  request<SummaryResponse>(`${A}/summary`, { query: { ...p }, signal });

export const getTimeseries = (
  p: AnalyticsParams & { granularity: Granularity; metric: Metric },
  signal?: AbortSignal,
) => request<TimeseriesResponse>(`${A}/timeseries`, { query: { ...p }, signal });

export const getTopProducts = (p: AnalyticsParams & { limit?: number }, signal?: AbortSignal) =>
  request<TopProductsResponse>(`${A}/top-products`, { query: { ...p }, signal });

/** by-region does not accept a `region` filter (contract). */
export const getByRegion = (p: Omit<AnalyticsParams, "region">, signal?: AbortSignal) =>
  request<BreakdownResponse>(`${A}/by-region`, { query: { ...p }, signal });

/** by-channel does not accept a `channel` filter (contract). */
export const getByChannel = (p: Omit<AnalyticsParams, "channel">, signal?: AbortSignal) =>
  request<BreakdownResponse>(`${A}/by-channel`, { query: { ...p }, signal });

export const listStores = (page: { limit?: number; cursor?: string | null } = {}) =>
  request<StorePage>("/api/v1/stores", { query: { ...page } });
export const createStore = (body: StoreCreate) =>
  request<StoreCreated>("/api/v1/stores", { method: "POST", body });

export const listProducts = (page: { limit?: number; cursor?: string | null } = {}) =>
  request<ProductPage>("/api/v1/products", { query: { ...page } });
export const createProduct = (body: ProductCreate) =>
  request<ProductOut>("/api/v1/products", { method: "POST", body });

export const createExport = (body: ExportCreate) =>
  request<ExportJob>("/api/v1/exports", { method: "POST", body });
export const getExport = (id: string) => request<ExportJob>(`/api/v1/exports/${encodeURIComponent(id)}`);
export const getExportDownload = (id: string) =>
  request<DownloadResponse>(`/api/v1/exports/${encodeURIComponent(id)}/download`);

/** Streamed CSV: fetched with the auth header as a blob, then saved via a temporary link. */
export async function downloadCsv(params: Record<string, string | undefined>): Promise<string> {
  const { blob, filename } = await requestBlob("/api/v1/exports/csv", { query: params });
  const name = filename ?? "sales_export.csv";
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
  return name;
}
