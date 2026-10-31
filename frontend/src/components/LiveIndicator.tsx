import { useEffect, useState } from "react";
import type { LiveStatus } from "../realtime/useLiveDashboard";

const LABELS: Record<LiveStatus, { icon: string; text: string }> = {
  connected: { icon: "●", text: "Live: connected" },
  connecting: { icon: "◌", text: "Connecting…" },
  reconnecting: { icon: "◐", text: "Reconnecting…" },
  offline: { icon: "✕", text: "Offline: showing last loaded data" },
};

export function formatAgo(lastUpdatedAt: number | null, now: number): string {
  if (lastUpdatedAt === null) return "No live update yet";
  const s = Math.max(0, Math.floor((now - lastUpdatedAt) / 1000));
  return `Last updated ${s} second${s === 1 ? "" : "s"} ago`;
}

interface Props {
  status: LiveStatus;
  lastUpdatedAt: number | null;
}

/** Status is conveyed by colour, an icon and text. Only the status text is a live region. */
export default function LiveIndicator({ status, lastUpdatedAt }: Props) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const { icon, text } = LABELS[status];
  return (
    <div className={`live-indicator live-${status}`} data-status={status}>
      <span aria-live="polite" role="status" className="live-status">
        <span aria-hidden="true" className="live-icon">
          {icon}
        </span>{" "}
        {text}
      </span>
      <span className="live-ago">{formatAgo(lastUpdatedAt, now)}</span>
    </div>
  );
}
