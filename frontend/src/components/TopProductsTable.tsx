import type { TopProductsResponse } from "../api/types";
import { formatInt, formatMoney } from "../lib/format";
import WidgetState from "./WidgetState";

interface Props {
  data?: TopProductsResponse;
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => void;
}

export default function TopProductsTable({ data, isLoading, error, onRetry }: Props) {
  const items = data?.items ?? [];
  return (
    <section aria-labelledby="top-heading" className="widget">
      <h2 id="top-heading">Top products by revenue</h2>
      <WidgetState
        label="top products"
        isLoading={isLoading}
        error={error}
        isEmpty={items.length === 0}
        emptyMessage="No product sales in the selected period."
        onRetry={onRetry}
      >
        <table className="data-table">
          <caption className="visually-hidden">Top products ranked by revenue</caption>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">SKU</th>
              <th scope="col">Product</th>
              <th scope="col" className="num">
                Units
              </th>
              <th scope="col" className="num">
                Revenue
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((p, i) => (
              <tr key={p.product_id}>
                <td>{i + 1}</td>
                <td>{p.sku}</td>
                <td>{p.name}</td>
                <td className="num">{formatInt(p.units)}</td>
                <td className="num">{formatMoney(p.revenue, data?.meta.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </WidgetState>
    </section>
  );
}
