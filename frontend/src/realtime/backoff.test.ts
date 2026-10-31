import { describe, expect, it } from "vitest";
import { BACKOFF_CAP_MS, backoffDelay } from "./backoff";

describe("backoffDelay", () => {
  it("grows monotonically with a fixed rng", () => {
    const delays = Array.from({ length: 10 }, (_, i) => backoffDelay(i, () => 0.5));
    for (let i = 1; i < delays.length; i++) expect(delays[i]).toBeGreaterThanOrEqual(delays[i - 1]);
    expect(delays[1]).toBeGreaterThan(delays[0]);
  });

  it("never exceeds the cap", () => {
    for (const a of [5, 6, 10, 50, 1000]) expect(backoffDelay(a, () => 0.999999)).toBeLessThanOrEqual(BACKOFF_CAP_MS);
    expect(backoffDelay(100, () => 1)).toBe(BACKOFF_CAP_MS);
  });

  it("keeps jitter within [nominal/2, nominal]", () => {
    expect(backoffDelay(0, () => 0)).toBe(500);
    expect(backoffDelay(0, () => 1)).toBe(1000);
    expect(backoffDelay(3, () => 0)).toBe(4000);
    expect(backoffDelay(3, () => 1)).toBe(8000);
    for (let i = 0; i < 200; i++) {
      const d = backoffDelay(2);
      expect(d).toBeGreaterThanOrEqual(2000);
      expect(d).toBeLessThanOrEqual(4000);
    }
  });

  it("treats negative attempts as 0", () => {
    expect(backoffDelay(-3, () => 1)).toBe(1000);
  });
});
