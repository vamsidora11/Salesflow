export interface Batcher<T> {
  push(item: T): void;
  /** Flush immediately (no-op when empty). */
  flush(): void;
  /** Stop the timer and drop anything queued. */
  stop(): void;
}

export interface BatcherOptions<T> {
  intervalMs: number;
  onFlush: (items: T[]) => void;
  /** Items with the same key are merged into one entry. */
  key?: (item: T) => string;
  merge?: (previous: T, next: T) => T;
}

/**
 * Queues items and flushes them every `intervalMs`. The interval only runs while
 * something is queued, and an empty queue never calls `onFlush`.
 */
export function createBatcher<T>({ intervalMs, onFlush, key, merge }: BatcherOptions<T>): Batcher<T> {
  let queue = new Map<string, T>();
  let counter = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const flush = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    if (queue.size === 0) return;
    const items = Array.from(queue.values());
    queue = new Map();
    onFlush(items);
  };

  return {
    push(item) {
      const k = key ? key(item) : `#${counter++}`;
      const prev = queue.get(k);
      queue.set(k, prev !== undefined && merge ? merge(prev, item) : item);
      if (timer === null) timer = setTimeout(flush, intervalMs);
    },
    flush,
    stop() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      queue = new Map();
    },
  };
}

/** Leading + trailing throttle: `fn` runs at most once per `intervalMs`. */
export function createThrottle(fn: () => void, intervalMs: number, now: () => number = Date.now) {
  let last = -Infinity;
  let timer: ReturnType<typeof setTimeout> | null = null;
  return {
    call() {
      const elapsed = now() - last;
      if (elapsed >= intervalMs) {
        last = now();
        fn();
      } else if (timer === null) {
        timer = setTimeout(() => {
          timer = null;
          last = now();
          fn();
        }, intervalMs - elapsed);
      }
    },
    cancel() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
    },
  };
}
