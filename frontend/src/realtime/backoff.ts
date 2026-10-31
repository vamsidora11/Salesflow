export const BACKOFF_BASE_MS = 1000;
export const BACKOFF_CAP_MS = 30_000;

/**
 * Exponential backoff with "equal jitter": the nominal delay doubles per attempt up
 * to the cap, and the result is uniformly spread over [nominal/2, nominal].
 */
export function backoffDelay(attempt: number, rng: () => number = Math.random): number {
  const a = Math.max(0, Math.floor(attempt));
  const nominal = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** Math.min(a, 30));
  return Math.round(nominal / 2 + rng() * (nominal / 2));
}
