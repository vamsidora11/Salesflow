# WebSocket protocol

Endpoint: `WS /api/v1/ws/dashboard` (through nginx: `ws(s)://<host>/api/v1/ws/dashboard`).
WebSockets are not part of OpenAPI, so the protocol is documented here.

Live updates are **best-effort**. The database is the source of truth: after any gap
(reconnect, `resync_required`) the client refetches the REST endpoints.

## Authentication

The token is **never** put in the URL (URLs end up in logs and browser history).

1. Client connects.
2. Client sends, within **5 seconds**: `{"type": "auth", "token": "<jwt from /api/v1/auth/login>"}`.
3. Server replies `{"type": "auth.ok", "seq": <current tenant seq>}`, or sends
   `{"type": "auth.error"}` and closes.

Otherwise (no message, bad first message, bad/expired token) the server closes with code **4401**.
The server also closes with 4401 when the token expires mid-connection, and with **4408** if the
client stops reading (a send blocked for more than 5 s). Clients should reconnect with
exponential backoff and re-authenticate with a fresh token.

## Server -> client

| Message | Meaning |
|---|---|
| `{"type":"auth.ok","seq":N}` | Authenticated. `seq` is the tenant's latest sequence number. |
| `{"type":"auth.error"}` | Authentication failed; the socket is closed (4401). |
| `{"type":"update",...}` | New totals for one (store, hour bucket); see below. |
| `{"type":"heartbeat"}` | Sent after 25 s of silence. |
| `{"type":"resync_required"}` | Client was too slow and updates were dropped. Refetch everything. |
| `{"type":"pong"}` | Reply to `ping`. |
| `{"type":"subscribed"/"unsubscribed","store_id":..,"region":..}` | Acknowledges a filter change. |
| `{"type":"error","detail":"..."}` | Malformed client message (connection stays open). |

### `update`

```json
{
  "type": "update",
  "seq": 1042,
  "bucket": "2026-10-30T14:00:00+00:00",
  "store_id": "3f2a9c1e-...",
  "region": "Europe",
  "revenue": "1523.40",
  "units": 88,
  "delta_revenue": "44.98",
  "delta_units": 3
}
```

* `revenue` / `units`: the **new total** for `(store_id, bucket)` read from the database after the batch commit. Money is a string.
* `delta_revenue` / `delta_units`: what the committed batch **added** to that total (duplicates never count). Clients that keep aggregated series use these to adjust their cache; totals let them verify.
* `seq`: per-tenant, monotonically increasing (Redis `INCR`). A client that filters by store sees gaps; that is normal.
* Updates for the same `(bucket, store_id)` waiting in a slow client's buffer are **coalesced**: latest totals, summed deltas, highest `seq`.

## Client -> server

| Message | Effect |
|---|---|
| `{"type":"auth","token":"..."}` | Must be the first message. |
| `{"type":"subscribe","store_id":"..."}` / `{"region":"..."}` | Restrict updates. Several filters are OR-ed. No filter = all stores of the tenant. |
| `{"type":"unsubscribe","store_id":"..."}` / `{"region":"..."}` | Remove a filter. |
| `{"type":"ping"}` | Server answers `pong`. |

## Isolation and backpressure

* Updates are delivered only to connections of the tenant in the JWT; the tenant of a message is
  taken from its Redis channel (`analytics:<tenant_id>`), never from message content.
* Each connection buffers at most `WS_MAX_PENDING_UPDATES` (500) distinct `(bucket, store)` keys.
  Beyond that the backlog is discarded and `resync_required` is sent instead.

## Recovery

After reconnect, or on `resync_required`, refetch `GET /api/v1/analytics/*` for the visible filters.
`auth.ok.seq` lets a client detect that it missed updates while disconnected.
