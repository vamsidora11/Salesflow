import { useId, type ReactNode } from "react";

interface Props {
  title: string;
  /** Plain-language description of what the chart shows (read by assistive tech). */
  summary: string;
  columns: string[];
  rows: (string | number)[][];
  children: ReactNode;
}

/** A chart with an accessible text summary and a collapsible data table alternative. */
export default function ChartCard({ title, summary, columns, rows, children }: Props) {
  const id = useId();
  return (
    <div className="chart-card">
      <p id={`${id}-summary`} className="chart-summary">
        {summary}
      </p>
      <div className="chart-box" role="img" aria-label={`${title}. ${summary}`}>
        {children}
      </div>
      <details className="chart-table">
        <summary>View data as table</summary>
        <table>
          <caption className="visually-hidden">{title}</caption>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                {r.map((cell, j) => (
                  <td key={j}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  );
}
