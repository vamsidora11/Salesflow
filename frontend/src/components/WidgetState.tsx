import type { ReactNode } from "react";
import { errorMessage } from "../lib/format";

interface Props {
  label: string;
  isLoading: boolean;
  error?: unknown;
  isEmpty?: boolean;
  emptyMessage?: string;
  onRetry?: () => void;
  children: ReactNode;
}

/** Explicit loading, error and empty states shared by every dashboard widget. */
export default function WidgetState({ label, isLoading, error, isEmpty, emptyMessage, onRetry, children }: Props) {
  if (isLoading) {
    return (
      <p className="state state-loading" role="status">
        Loading {label}…
      </p>
    );
  }
  if (error) {
    return (
      <div className="state state-error" role="alert">
        <p>
          Could not load {label}: {errorMessage(error)}
        </p>
        {onRetry && (
          <button type="button" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    );
  }
  if (isEmpty) {
    return <p className="state state-empty">{emptyMessage ?? `No ${label} for the selected filters.`}</p>;
  }
  return <>{children}</>;
}
