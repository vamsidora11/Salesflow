import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SummaryResponse } from "../api/types";
import { ChannelChart } from "./Charts";
import KpiCards from "./KpiCards";
import LiveIndicator from "./LiveIndicator";
import TopProductsTable from "./TopProductsTable";

describe("LiveIndicator", () => {
  it.each([
    ["connected", /connected/i],
    ["connecting", /connecting/i],
    ["reconnecting", /reconnecting/i],
    ["offline", /offline/i],
  ] as const)("renders %s with text", (status, re) => {
    render(<LiveIndicator status={status} lastUpdatedAt={null} />);
    expect(screen.getByRole("status")).toHaveTextContent(re);
    expect(screen.getByText(/no live update yet/i)).toBeInTheDocument();
  });

  describe("ticking", () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.setSystemTime(new Date("2026-10-30T14:00:00Z"));
    });
    afterEach(() => vi.useRealTimers());

    it("updates the 'seconds ago' text every second", () => {
      render(<LiveIndicator status="connected" lastUpdatedAt={Date.now()} />);
      expect(screen.getByText("Last updated 0 seconds ago")).toBeInTheDocument();
      act3();
      expect(screen.getByText("Last updated 3 seconds ago")).toBeInTheDocument();
    });
  });
});

import { act } from "@testing-library/react";
function act3() {
  act(() => {
    vi.advanceTimersByTime(3000);
  });
}

const meta = { generated_at: "2026-10-30T14:00:00Z", data_freshness_seconds: 1, currency: "USD" };

describe("widget states", () => {
  it("TopProductsTable shows the empty state", () => {
    render(<TopProductsTable data={{ items: [], meta }} isLoading={false} />);
    expect(screen.getByText(/no product sales/i)).toBeInTheDocument();
  });

  it("TopProductsTable shows loading", () => {
    render(<TopProductsTable isLoading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading top products/i);
  });

  it("TopProductsTable shows rows with formatted money", () => {
    render(
      <TopProductsTable
        isLoading={false}
        data={{ meta, items: [{ product_id: "p1", sku: "SKU-1", name: "Widget", revenue: "1234.50", units: 7 }] }}
      />,
    );
    expect(screen.getByText("SKU-1")).toBeInTheDocument();
    expect(screen.getByText(/1,234\.50/)).toBeInTheDocument();
  });

  it("TopProductsTable shows an error with retry", async () => {
    const onRetry = vi.fn();
    render(<TopProductsTable isLoading={false} error={new Error("boom")} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/boom/);
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("KpiCards shows loading, error and values", () => {
    const { rerender } = render(<KpiCards isLoading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    rerender(<KpiCards isLoading={false} error={new Error("nope")} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/nope/);
    const data: SummaryResponse = { revenue: "100.00", units: 4, transactions: 2, average_order_value: "50.00", meta };
    rerender(<KpiCards isLoading={false} data={data} />);
    expect(screen.getByText("Transactions")).toBeInTheDocument();
    expect(screen.getByText("Average order value")).toBeInTheDocument();
  });

  it("KpiCards shows the empty state for zero sales", () => {
    const data: SummaryResponse = { revenue: "0.00", units: 0, transactions: 0, average_order_value: "0.00", meta };
    render(<KpiCards isLoading={false} data={data} />);
    expect(screen.getByText(/no sales in the selected period/i)).toBeInTheDocument();
  });

  it("charts show an empty state and offer a data table alternative", () => {
    const { unmount } = render(<ChannelChart isLoading={false} data={{ items: [], meta }} />);
    expect(screen.getByText(/no channel sales/i)).toBeInTheDocument();
    unmount();
    render(
      <ChannelChart
        isLoading={false}
        data={{ meta, items: [{ label: "online", revenue: "75.00", units: 3, transactions: 2 }, { label: "pos", revenue: "25.00", units: 1, transactions: 1 }] }}
      />,
    );
    expect(screen.getByText("View data as table")).toBeInTheDocument();
    expect(screen.getByText(/online 75\.0%/)).toBeInTheDocument();
  });
});
