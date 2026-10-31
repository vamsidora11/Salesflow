/** Money arrives as strings; Number() is used here only for display and charting. */
export function formatMoney(value: string | number, currency = "USD"): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(n);
  } catch {
    return n.toFixed(2);
  }
}

export function formatInt(value: number | string): string {
  const n = Number(value);
  return Number.isFinite(n) ? new Intl.NumberFormat().format(n) : "-";
}

export function formatHour(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function formatHourShort(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : "Something went wrong.";
}

/** Okabe-Ito colour-blind-safe palette (yellow omitted: too low contrast on white). */
export const PALETTE = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9", "#E69F00", "#000000"] as const;
