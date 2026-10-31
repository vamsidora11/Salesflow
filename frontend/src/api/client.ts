import { clearSession, getSession } from "./session";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

type QueryValue = string | number | boolean | null | undefined;

export interface RequestOptions {
  method?: "GET" | "POST";
  query?: Record<string, QueryValue>;
  body?: unknown;
  signal?: AbortSignal;
  /** Skip the Authorization header and the 401 redirect (login). */
  anonymous?: boolean;
}

let unauthorizedHandler: () => void = () => {
  clearSession();
  if (window.location.pathname !== "/login") window.location.assign("/login");
};

/** Called on any 401 from an authenticated request. Replaceable for tests. */
export function setUnauthorizedHandler(fn: () => void): void {
  unauthorizedHandler = fn;
}

export function handleUnauthorized(): void {
  unauthorizedHandler();
}

export function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function toError(res: Response): Promise<ApiError> {
  let detail = res.statusText || `HTTP ${res.status}`;
  try {
    const data: unknown = await res.json();
    if (data && typeof data === "object" && "detail" in data) {
      const d = (data as { detail: unknown }).detail;
      if (typeof d === "string") detail = d;
      else if (Array.isArray(d)) {
        detail = d
          .map((e) => (e && typeof e === "object" && "msg" in e ? String((e as { msg: unknown }).msg) : String(e)))
          .join("; ");
      }
    }
  } catch {
    /* non-JSON body: keep status text */
  }
  return new ApiError(res.status, detail);
}

async function send(path: string, opts: RequestOptions, accept: string): Promise<Response> {
  const headers: Record<string, string> = { Accept: accept };
  const session = opts.anonymous ? null : getSession();
  if (session) headers.Authorization = `Bearer ${session.token}`;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method: opts.method ?? "GET",
      headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "Network error: the API is unreachable.");
  }
  if (!res.ok) {
    if (res.status === 401 && !opts.anonymous) unauthorizedHandler();
    throw await toError(res);
  }
  return res;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const res = await send(path, opts, "application/json");
  return (await res.json()) as T;
}

export async function requestBlob(
  path: string,
  opts: RequestOptions = {},
): Promise<{ blob: Blob; filename: string | null }> {
  const res = await send(path, opts, "text/csv");
  const cd = res.headers.get("Content-Disposition") ?? "";
  const match = /filename="?([^";]+)"?/i.exec(cd);
  return { blob: await res.blob(), filename: match ? match[1] : null };
}
