export interface Subscription {
  store_id?: string;
  region?: string;
}

/**
 * Server filters are OR-ed, while the REST filters are AND-ed. When both are set we
 * subscribe to the store only (the narrower one); updates are also filtered client-side.
 */
export function desiredSubscription(f: { store_id?: string; region?: string }): Subscription {
  if (f.store_id) return { store_id: f.store_id };
  if (f.region) return { region: f.region };
  return {};
}

export type SubMessage =
  | { type: "subscribe" | "unsubscribe"; store_id: string }
  | { type: "subscribe" | "unsubscribe"; region: string };

/** Messages needed to move from the `current` subscription to the `desired` one. */
export function diffSubscriptions(current: Subscription, desired: Subscription): SubMessage[] {
  const out: SubMessage[] = [];
  if (current.store_id && current.store_id !== desired.store_id)
    out.push({ type: "unsubscribe", store_id: current.store_id });
  if (current.region && current.region !== desired.region) out.push({ type: "unsubscribe", region: current.region });
  if (desired.store_id && desired.store_id !== current.store_id)
    out.push({ type: "subscribe", store_id: desired.store_id });
  if (desired.region && desired.region !== current.region) out.push({ type: "subscribe", region: desired.region });
  return out;
}
