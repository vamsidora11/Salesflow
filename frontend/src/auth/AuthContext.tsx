import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { login as apiLogin } from "../api/endpoints";
import { clearSession, getSession, setSession, subscribeSession, type Session } from "../api/session";
import type { Role } from "../api/types";

export interface AuthValue {
  session: Session | null;
  token: string | null;
  role: Role | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [session, setLocal] = useState<Session | null>(() => getSession());

  useEffect(() => subscribeSession(() => setLocal(getSession())), []);

  // Expire the session on time instead of waiting for the next 401.
  useEffect(() => {
    if (!session) return;
    const ms = session.expiresAt - Date.now();
    const id = setTimeout(() => clearSession(), Math.min(Math.max(ms, 0), 2 ** 31 - 1));
    return () => clearTimeout(id);
  }, [session]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await apiLogin(email, password);
    setSession({ token: res.access_token, role: res.role, expiresAt: Date.now() + res.expires_in * 1000 });
  }, []);

  const logout = useCallback(() => {
    clearSession();
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo<AuthValue>(
    () => ({
      session,
      token: session?.token ?? null,
      role: session?.role ?? null,
      isAuthenticated: !!session,
      login,
      logout,
    }),
    [session, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
