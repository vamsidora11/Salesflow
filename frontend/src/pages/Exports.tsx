import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError } from "../api/client";
import { createExport, downloadCsv, getExport, getExportDownload, listStores } from "../api/endpoints";
import { CHANNELS, canAnalyze, type Channel, type ExportFormat, type ExportJob, type Granularity } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { errorMessage } from "../lib/format";

const JOBS_KEY = "salesflow.exportJobs";
const MAX_JOBS = 20;

export function loadJobIds(): string[] {
  try {
    const raw: unknown = JSON.parse(sessionStorage.getItem(JOBS_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

export function rememberJobId(id: string): string[] {
  const ids = [id, ...loadJobIds().filter((x) => x !== id)].slice(0, MAX_JOBS);
  try {
    sessionStorage.setItem(JOBS_KEY, JSON.stringify(ids));
  } catch {
    /* memory only */
  }
  return ids;
}

const isActive = (j?: ExportJob) => j?.status === "queued" || j?.status === "running";

const isoDay = (d: Date) => d.toISOString().slice(0, 10);

function JobRow({ id }: { id: string }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const q = useQuery({
    queryKey: ["exports", id],
    queryFn: () => getExport(id),
    refetchInterval: (query) => (isActive(query.state.data) ? 2000 : false),
    retry: false,
  });
  const job = q.data;

  async function download() {
    setBusy(true);
    setMsg(null);
    try {
      const { url } = await getExportDownload(id);
      // Presigned URL: open without logging it.
      const a = document.createElement("a");
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      if (e instanceof ApiError && e.status === 410) {
        setMsg("This export has expired. Please create a new one.");
        void q.refetch();
      } else if (e instanceof ApiError && e.status === 409) {
        setMsg("The file is not ready yet. Try again in a moment.");
        void q.refetch();
      } else setMsg(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="job">
      <div>
        <strong>{job ? job.format.toUpperCase() : "Export"}</strong> <code>{id.slice(0, 8)}</code>
      </div>
      {q.isLoading && <p role="status">Loading status…</p>}
      {q.isError && (
        <p role="alert" className="field-error">
          {q.error instanceof ApiError && q.error.status === 404 ? "Job not found." : errorMessage(q.error)}
        </p>
      )}
      {job && (
        <p>
          Status: <span className={`badge badge-${job.status}`}>{job.status}</span>
          {job.row_count !== null && <> &middot; {job.row_count.toLocaleString()} rows</>}
          {job.status === "expired" && <> &middot; file expired</>}
        </p>
      )}
      {job?.error && (
        <p role="alert" className="field-error">
          Error: {job.error}
        </p>
      )}
      {job?.status === "succeeded" && (
        <button type="button" onClick={() => void download()} disabled={busy}>
          {busy ? "Preparing…" : "Download"}
        </button>
      )}
      <div aria-live="polite">{msg && <p className="field-error">{msg}</p>}</div>
    </li>
  );
}

export default function Exports() {
  const { role } = useAuth();
  const today = new Date();
  const [format, setFormat] = useState<ExportFormat>("xlsx");
  const [from, setFrom] = useState(isoDay(new Date(today.getTime() - 7 * 86_400_000)));
  const [to, setTo] = useState(isoDay(today));
  const [granularity, setGranularity] = useState<Granularity>("day");
  const [storeId, setStoreId] = useState("");
  const [region, setRegion] = useState("");
  const [channel, setChannel] = useState<Channel | "">("");
  const [jobIds, setJobIds] = useState<string[]>(loadJobIds);
  const [status, setStatus] = useState<string | null>(null);
  const [csvBusy, setCsvBusy] = useState(false);

  const allowed = canAnalyze(role);
  const storesQ = useQuery({
    queryKey: ["stores", "export-form"],
    queryFn: () => listStores({ limit: 200 }),
    enabled: allowed,
  });
  const stores = storesQ.data?.items ?? [];
  const regions = Array.from(new Set(stores.map((s) => s.region))).sort();

  const rangeOk = !!from && !!to && from <= to;
  const range = () => {
    const end = new Date(`${to}T00:00:00Z`);
    end.setUTCDate(end.getUTCDate() + 1);
    return { from: new Date(`${from}T00:00:00Z`).toISOString(), to: end.toISOString() };
  };
  const dims = () => ({
    store_id: storeId || undefined,
    region: region || undefined,
    channel: channel || undefined,
  });

  const create = useMutation({
    mutationFn: () =>
      createExport({
        format,
        filters: { ...range(), granularity, store_id: storeId || null, region: region || null, channel: channel || null },
      }),
    onSuccess: (job) => {
      setJobIds(rememberJobId(job.id));
      setStatus(`Export job created (${job.status}).`);
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setStatus(null);
    if (rangeOk) create.mutate();
  }

  async function quickCsv() {
    setCsvBusy(true);
    setStatus("Preparing CSV download…");
    try {
      const name = await downloadCsv({ ...range(), granularity, ...dims() });
      setStatus(`Downloaded ${name}.`);
    } catch (e) {
      setStatus(
        e instanceof ApiError && e.status === 413
          ? "Too many rows for a direct download. Use an asynchronous export instead."
          : `CSV download failed: ${errorMessage(e)}`,
      );
    } finally {
      setCsvBusy(false);
    }
  }

  if (!allowed) {
    return (
      <div>
        <h1>Exports</h1>
        <p className="state state-empty">Exports require the analyst or admin role.</p>
      </div>
    );
  }

  return (
    <div>
      <h1>Exports</h1>
      <section className="widget" aria-labelledby="export-form-heading">
        <h2 id="export-form-heading">Request an export</h2>
        <form className="export-form" onSubmit={onSubmit}>
          <div className="field">
            <label htmlFor="x-format">Format</label>
            <select id="x-format" value={format} onChange={(e) => setFormat(e.target.value as ExportFormat)}>
              <option value="xlsx">Excel (xlsx), asynchronous</option>
              <option value="csv">CSV, asynchronous</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="x-from">From (UTC)</label>
            <input id="x-from" type="date" required value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="x-to">To (UTC, inclusive)</label>
            <input id="x-to" type="date" required value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="x-gran">Granularity</label>
            <select id="x-gran" value={granularity} onChange={(e) => setGranularity(e.target.value as Granularity)}>
              <option value="day">Day</option>
              <option value="hour">Hour</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="x-store">Store (optional)</label>
            <select id="x-store" value={storeId} onChange={(e) => setStoreId(e.target.value)}>
              <option value="">All stores</option>
              {stores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="x-region">Region (optional)</label>
            <select id="x-region" value={region} onChange={(e) => setRegion(e.target.value)}>
              <option value="">All regions</option>
              {regions.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="x-channel">Channel (optional)</label>
            <select id="x-channel" value={channel} onChange={(e) => setChannel(e.target.value as Channel | "")}>
              <option value="">All channels</option>
              {CHANNELS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          {!rangeOk && (
            <p className="field-error" role="alert">
              Choose a start date on or before the end date.
            </p>
          )}
          <div className="actions">
            <button type="submit" disabled={!rangeOk || create.isPending}>
              {create.isPending ? "Requesting…" : "Create export job"}
            </button>
            <button type="button" className="secondary" disabled={!rangeOk || csvBusy} onClick={() => void quickCsv()}>
              Quick CSV download
            </button>
          </div>
        </form>
        <div aria-live="polite" className="status-line">
          {create.isError && (
            <p className="field-error" role="alert">
              Could not create export: {errorMessage(create.error)}
            </p>
          )}
          {status && <p>{status}</p>}
        </div>
      </section>

      <section className="widget" aria-labelledby="jobs-heading">
        <h2 id="jobs-heading">Your export jobs (this session)</h2>
        {jobIds.length === 0 ? (
          <p className="state state-empty">No export jobs yet.</p>
        ) : (
          <ul className="jobs">
            {jobIds.map((id) => (
              <JobRow key={id} id={id} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
