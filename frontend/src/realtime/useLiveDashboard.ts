import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { handleUnauthorized } from "../api/client";
import type { SummaryResponse, TimeseriesResponse } from "../api/types";
import { DASHBOARD, keys, type FilterValues } from "../queryKeys";
import {
  applyUpdateToSeries,
  applyUpdateToSummary,
  isLiveUpdate,
  mergeUpdates,
  updateKey,
  updateMatchesFilters,
  type LiveUpdate,
} from "./applyUpdate";
import { backoffDelay } from "./backoff";
import { createBatcher, createThrottle } from "./batcher";
import { desiredSubscription, diffSubscriptions, type Subscription } from "./subscriptions";

export type LiveStatus = "connecting" | "connected" | "reconnecting" | "offline";

export const BATCH_INTERVAL_MS = 500;
export const CHANNEL_INVALIDATE_MS = 5000;
/** No message (heartbeats arrive every 25 s) for this long means the socket is dead. */
export const WATCHDOG_MS = 60_000;
/** After this many failed attempts in a row the indicator shows "offline" (retries continue). */
export const OFFLINE_AFTER_ATTEMPTS = 5;

export interface UseLiveDashboardOptions {
  token: string | null;
  /** Viewers (and logged-out users) must not open the socket. */
  enabled?: boolean;
  filters: FilterValues;
  /** Query key of the summary the live deltas may be applied to (null = do not touch). */
  summaryKey?: QueryKey | null;
  onUnauthorized?: () => void;
}

export interface LiveDashboardState {
  status: LiveStatus;
  lastUpdatedAt: number | null;
}

export function wsUrl(loc: Pick<Location, "protocol" | "host"> = window.location): string {
  return `${loc.protocol === "https:" ? "wss" : "ws"}://${loc.host}/api/v1/ws/dashboard`;
}

export function useLiveDashboard(options: UseLiveDashboardOptions): LiveDashboardState {
  const { token, enabled = true } = options;
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<LiveStatus>("connecting");
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);

  const optsRef = useRef(options);
  optsRef.current = options;
  const syncRef = useRef<(() => void) | null>(null);
  const filterSig = JSON.stringify(desiredSubscription(options.filters));

  useEffect(() => {
    if (!enabled || !token) {
      setStatus("offline");
      return;
    }
    setStatus("connecting");

    let disposed = false;
    let stopped = false; // unauthorised: never reconnect
    let ws: WebSocket | null = null;
    let authed = false;
    let attempt = 0;
    let everConnected = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let watchdog: ReturnType<typeof setTimeout> | null = null;
    let sub: Subscription = {};

    const invalidate = () => {
      void queryClient.invalidateQueries({ queryKey: [DASHBOARD] });
    };
    const throttledInvalidate = createThrottle(invalidate, CHANNEL_INVALIDATE_MS);

    const applyBatch = (items: LiveUpdate[]) => {
      const { filters, summaryKey } = optsRef.current;
      const relevant = items.filter((u) => updateMatchesFilters(u, filters));
      if (relevant.length === 0) return;
      setLastUpdatedAt(Date.now());
      if (filters.channel) {
        // Updates carry no channel, so deltas cannot be attributed; refetch instead.
        throttledInvalidate.call();
        return;
      }
      const now = new Date();
      for (const metric of ["revenue", "units"] as const) {
        queryClient.setQueryData<TimeseriesResponse>(keys.hourly(metric, filters), (old) =>
          old
            ? { ...old, points: relevant.reduce((pts, u) => applyUpdateToSeries(pts, u, metric, now), old.points) }
            : old,
        );
      }
      if (summaryKey) {
        queryClient.setQueryData<SummaryResponse>(summaryKey, (old) =>
          old ? relevant.reduce((s, u) => applyUpdateToSummary(s, u), old) : old,
        );
      }
    };

    const batcher = createBatcher<LiveUpdate>({
      intervalMs: BATCH_INTERVAL_MS,
      key: updateKey,
      merge: mergeUpdates,
      onFlush: applyBatch,
    });

    const clearWatchdog = () => {
      if (watchdog !== null) clearTimeout(watchdog);
      watchdog = null;
    };
    const armWatchdog = (sock: WebSocket) => {
      clearWatchdog();
      watchdog = setTimeout(() => sock.close(), WATCHDOG_MS);
    };

    const sync = () => {
      if (!ws || !authed || ws.readyState !== 1) return;
      const desired = desiredSubscription(optsRef.current.filters);
      for (const msg of diffSubscriptions(sub, desired)) ws.send(JSON.stringify(msg));
      sub = desired;
    };
    syncRef.current = sync;

    const failAuth = () => {
      if (stopped) return;
      stopped = true;
      batcher.stop();
      clearWatchdog();
      if (retryTimer !== null) clearTimeout(retryTimer);
      setStatus("offline");
      (optsRef.current.onUnauthorized ?? handleUnauthorized)();
    };

    const scheduleReconnect = () => {
      if (disposed || stopped) return;
      const offline = attempt >= OFFLINE_AFTER_ATTEMPTS || (typeof navigator !== "undefined" && navigator.onLine === false);
      setStatus(offline ? "offline" : "reconnecting");
      const delay = backoffDelay(attempt);
      attempt += 1;
      retryTimer = setTimeout(connect, delay);
    };

    function connect() {
      retryTimer = null;
      if (disposed || stopped) return;
      const sock = new WebSocket(wsUrl());
      ws = sock;
      authed = false;
      sub = {};

      // The token travels in the first message, never in the URL.
      sock.onopen = () => {
        if (ws !== sock) return;
        sock.send(JSON.stringify({ type: "auth", token }));
        armWatchdog(sock);
      };

      sock.onmessage = (ev: MessageEvent) => {
        if (ws !== sock || disposed) return;
        armWatchdog(sock);
        let msg: unknown;
        try {
          msg = JSON.parse(String(ev.data));
        } catch {
          return;
        }
        if (isLiveUpdate(msg)) {
          batcher.push(msg);
          return;
        }
        const type = (msg as { type?: unknown } | null)?.type;
        switch (type) {
          case "auth.ok": {
            const needsResync = everConnected || attempt > 0;
            authed = true;
            attempt = 0;
            everConnected = true;
            setStatus("connected");
            setLastUpdatedAt(Date.now());
            sync();
            // After any gap the database is the source of truth: refetch.
            if (needsResync) invalidate();
            break;
          }
          case "auth.error":
            failAuth();
            sock.close();
            break;
          case "resync_required":
            batcher.stop(); // queued deltas are superseded by the refetch
            invalidate();
            break;
          case "heartbeat":
          case "pong":
          case "subscribed":
          case "unsubscribed":
          default:
            break;
        }
      };

      sock.onerror = () => {
        /* a close event always follows */
      };

      sock.onclose = (ev: CloseEvent) => {
        if (ws !== sock) return;
        ws = null;
        authed = false;
        clearWatchdog();
        if (disposed) return;
        if (ev.code === 4401) {
          // Expired or invalid token. Do not hammer the server: go to login once.
          failAuth();
          return;
        }
        scheduleReconnect();
      };
    }

    const onOnline = () => {
      if (disposed || stopped || ws) return;
      if (retryTimer !== null) clearTimeout(retryTimer);
      setStatus("reconnecting");
      connect();
    };
    const onOffline = () => {
      if (!disposed) setStatus("offline");
    };
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);

    connect();

    return () => {
      disposed = true;
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      if (retryTimer !== null) clearTimeout(retryTimer);
      clearWatchdog();
      batcher.stop();
      throttledInvalidate.cancel();
      syncRef.current = null;
      if (ws) {
        const sock = ws;
        ws = null;
        sock.onopen = sock.onmessage = sock.onclose = sock.onerror = null;
        sock.close();
      }
    };
  }, [token, enabled, queryClient]);

  // Filter changes: unsubscribe/resubscribe on the live socket.
  useEffect(() => {
    syncRef.current?.();
  }, [filterSig]);

  return { status, lastUpdatedAt };
}
