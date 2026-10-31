import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createBatcher, createThrottle } from "./batcher";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("createBatcher", () => {
  it("flushes queued items once per interval", () => {
    const onFlush = vi.fn();
    const b = createBatcher<number>({ intervalMs: 500, onFlush });
    b.push(1);
    b.push(2);
    vi.advanceTimersByTime(499);
    expect(onFlush).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onFlush).toHaveBeenCalledTimes(1);
    expect(onFlush).toHaveBeenCalledWith([1, 2]);
  });

  it("does not flush when empty", () => {
    const onFlush = vi.fn();
    const b = createBatcher<number>({ intervalMs: 500, onFlush });
    vi.advanceTimersByTime(5000);
    b.flush();
    expect(onFlush).not.toHaveBeenCalled();
    b.push(1);
    vi.advanceTimersByTime(500);
    vi.advanceTimersByTime(5000);
    expect(onFlush).toHaveBeenCalledTimes(1);
  });

  it("merges items with the same key", () => {
    const onFlush = vi.fn();
    const b = createBatcher<{ k: string; n: number }>({
      intervalMs: 500,
      onFlush,
      key: (i) => i.k,
      merge: (p, n) => ({ k: n.k, n: p.n + n.n }),
    });
    b.push({ k: "a", n: 1 });
    b.push({ k: "b", n: 5 });
    b.push({ k: "a", n: 2 });
    vi.advanceTimersByTime(500);
    expect(onFlush).toHaveBeenCalledWith([
      { k: "a", n: 3 },
      { k: "b", n: 5 },
    ]);
  });

  it("stop drops pending items", () => {
    const onFlush = vi.fn();
    const b = createBatcher<number>({ intervalMs: 500, onFlush });
    b.push(1);
    b.stop();
    vi.advanceTimersByTime(1000);
    expect(onFlush).not.toHaveBeenCalled();
  });
});

describe("createThrottle", () => {
  it("runs at most once per interval with a trailing call", () => {
    const fn = vi.fn();
    const t = createThrottle(fn, 5000, () => Date.now());
    vi.setSystemTime(10_000);
    t.call();
    t.call();
    t.call();
    expect(fn).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(5000);
    expect(fn).toHaveBeenCalledTimes(2);
  });
});
