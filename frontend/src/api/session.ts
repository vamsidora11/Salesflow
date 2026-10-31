import type { Role } from "./types";

/**
 * Session storage tradeoff (XSS):
 * The JWT is kept in module memory and mirrored to sessionStorage so a page reload
 * does not log the user out. Any script running on this origin (an XSS bug or a
 * malicious dependency) can read sessionStorage. An httpOnly SameSite cookie would
 * not be readable from JS, but the API uses bearer tokens and the WebSocket
 * authenticates with a first message, so we accept this tradeoff and mitigate it with
 * short token lifetimes, no tokens in URLs or logs, and sessionStorage (cleared when
 * the tab closes) instead of localStorage.
 */
export interface Session {
  token: string;
  role: Role;
  /** Epoch milliseconds when the token expires (from `expires_in`). */
  expiresAt: number;
}

const KEY = "salesflow.session";
let memory: Session | null = null;
const listeners = new Set<() => void>();

function isValid(s: unknown): s is Session {
  if (!s || typeof s !== "object") return false;
  const o = s as Record<string, unknown>;
  return typeof o.token === "string" && typeof o.role === "string" && typeof o.expiresAt === "number";
}

export function getSession(): Session | null {
  if (!memory) {
    try {
      const raw = sessionStorage.getItem(KEY);
      const parsed: unknown = raw ? JSON.parse(raw) : null;
      if (isValid(parsed)) memory = parsed;
    } catch {
      memory = null;
    }
  }
  if (memory && memory.expiresAt <= Date.now()) {
    clearSession();
    return null;
  }
  return memory;
}

export function setSession(session: Session): void {
  memory = session;
  try {
    sessionStorage.setItem(KEY, JSON.stringify(session));
  } catch {
    /* storage unavailable: memory only */
  }
  listeners.forEach((l) => l());
}

export function clearSession(): void {
  memory = null;
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
