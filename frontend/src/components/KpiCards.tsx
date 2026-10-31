import type { SummaryResponse } from "../api/types";
import { formatInt, formatMoney } from "../lib/format";
import WidgetState from "./WidgetState";

interface Props {
  data?: SummaryResponse;
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
}

export default function KpiCards({ data, isLoading, error, onRetry }: Props) {
  const empty = !!data && data.transactions === 0 && data.units === 0 && Number(data.revenue) === 0;
  const currency = data?.meta.currency;
  return (
    <section aria-labelledby="kpi-heading" className="widget">
      <h2 id="kpi-heading">Key figures</h2>
      <WidgetState
        label="key figures"
        isLoading={isLoading}
        error={error}
        isEmpty={empty}
        emptyMessage="No sales in the selected period."
        onRetry={onRetry}
      >
        {data && (
          <dl className="kpi-grid">
            <div className="kpi">
              <dt>Revenue</dt>
              <dd>{formatMoney(data.revenue, currency)}</dd>
            </div>
            <div className="kpi">
              <dt>Units sold</dt>
              <dd>{formatInt(data.units)}</dd>
            </div>
            <div className="kpi">
              <dt>Transactions</dt>
              <dd>{formatInt(data.transactions)}</dd>
            </div>
            <div className="kpi">
              <dt>Average order value</dt>
              <dd>{formatMoney(data.average_order_value, currency)}</dd>
            </div>
          </dl>
        )}
      </WidgetState>
    </section>
  );
}
