import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import type { Role } from "../api/types";
import { AuthContext, type AuthValue } from "../auth/AuthContext";

export function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
}

export function authValue(role: Role | null): AuthValue {
  return {
    session: role ? { token: "tok", role, expiresAt: Date.now() + 3_600_000 } : null,
    token: role ? "tok" : null,
    role,
    isAuthenticated: !!role,
    login: async () => {},
    logout: () => {},
  };
}

export function renderApp(ui: ReactElement, opts: { role: Role | null; route?: string; client?: QueryClient }) {
  const client = opts.client ?? makeClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={authValue(opts.role)}>
        <MemoryRouter initialEntries={[opts.route ?? "/"]}>{children}</MemoryRouter>
      </AuthContext.Provider>
    </QueryClientProvider>
  );
  return { client, ...render(ui, { wrapper }) };
}

export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static reset() {
    FakeWebSocket.instances = [];
  }
  readyState = 0;
  sent: string[] = [];
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  closeCalls = 0;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.closeCalls += 1;
    this.readyState = 3;
    this.onclose?.({ code: 1000 } as CloseEvent);
  }
  // Test drivers
  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  receive(msg: unknown) {
    this.onmessage?.({ data: JSON.stringify(msg) } as MessageEvent);
  }
  serverClose(code: number) {
    this.readyState = 3;
    this.onclose?.({ code } as CloseEvent);
  }
  get messages(): Record<string, unknown>[] {
    return this.sent.map((s) => JSON.parse(s) as Record<string, unknown>);
  }
}
