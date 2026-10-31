import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderApp } from "../test/utils";
import Dashboard from "./Dashboard";

vi.mock("../api/endpoints", () => ({
  getSummary: vi.fn(),
  getTimeseries: vi.fn(),
  getTopProducts: vi.fn(),
  getByRegion: vi.fn(),
  getByChannel: vi.fn(),
  listStores: vi.fn(),
}));

import * as api from "../api/endpoints";

const meta = { generated_at: "2026-10-30T14:00:00Z", data_freshness_seconds: 1, currency: "USD" };

beforeEach(() => {
  vi.mocked(api.getSummary).mockResolvedValue({
    revenue: "100.00",
    units: 4,
    transactions: 2,
    average_order_value: "50.00",
    meta,
  });
});

describe("Dashboard", () => {
  it("viewer sees KPI cards and an analyst-role note, and no analyst queries run", async () => {
    renderApp(<Dashboard />, { role: "viewer" });
    expect(screen.getByText(/requires analyst role/i)).toBeInTheDocument();
    expect(await screen.findByText("Units sold")).toBeInTheDocument();
    expect(screen.queryByText(/hourly revenue/i)).not.toBeInTheDocument();
    expect(api.getTimeseries).not.toHaveBeenCalled();
    expect(api.getTopProducts).not.toHaveBeenCalled();
    expect(api.listStores).not.toHaveBeenCalled();
  });
});
