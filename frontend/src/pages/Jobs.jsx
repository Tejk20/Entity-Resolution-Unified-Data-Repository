import {
  BadgeCheck,
  CheckCircle2,
  ChevronDown,
  FileText,
  RotateCcw,
  Timer,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge, EmptyState, ProgressBar, Spinner, statusTone } from "../components/ui";
import { apiV1 } from "../lib/api";

const STAGE = [
  { regex: /mapping|uploaded/i, label: "Mapping" },
  { regex: /clean/i, label: "Cleaning" },
  { regex: /index/i, label: "Indexing" },
  { regex: /match|resolve/i, label: "Matching" },
];

export default function Jobs() {
  const [jobs, setJobs] = useState([]);
  const [mappings, setMappings] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [confirming, setConfirming] = useState(null);
  const poll = useRef(null);

  const load = useCallback(async () => {
    try {
      const list = await apiV1.jobs({ limit: 60 });
      setJobs(list);
      setLoading(false);
      setError(null);
    } catch (e) {
      setLoading(false);
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // live-poll only while something is still running
  useEffect(() => {
    clearInterval(poll.current);
    if (jobs.some((j) => !["Completed", "Failed"].includes(j.status))) {
      poll.current = setInterval(load, 2500);
    }
    return () => clearInterval(poll.current);
  }, [jobs, load]);

  async function toggle(id) {
    setOpenId((cur) => (cur === id ? null : id));
    if (openId !== id && !mappings[id]) {
      try {
        const m = await apiV1.mappingPreview(id).catch(() => null);
        if (m) setMappings((prev) => ({ ...prev, [id]: m }));
      } catch {
        /* mapping panel optional */
      }
    }
  }

  async function retry(jobId) {
    const j = await apiV1.retryJob(jobId);
    setJobs((prev) => prev.map((x) => (x.id === jobId ? j : x)));
  }

  async function confirm(job) {
    if (confirming) return;
    setConfirming(job.id);
    try {
      let tableMappings = {};
      let mapping = {};
      const preview = mappings[job.id] || (await apiV1.mappingPreview(job.id).catch(() => null));
      if (preview?.tables?.length) {
        for (const t of preview.tables) tableMappings[t.table_name] = { ...(t.mapping || {}) };
        mapping = Object.values(tableMappings).find((m) => Object.keys(m).length) || {};
      }
      const updated = await apiV1.confirmMapping(job.id, { mapping, table_mappings: tableMappings });
      setJobs((prev) => prev.map((x) => (x.id === job.id ? updated : x)));
      setError(null);
    } catch (e) {
      setError(e.message || "could not confirm mapping");
    } finally {
      setConfirming(null);
    }
  }

  const active = jobs.filter((j) => !["Completed", "Failed"].includes(j.status));
  const failed = jobs.filter((j) => j.status === "Failed").length;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-ink-900">Processing Jobs</h1>
          <p className="text-sm text-slate-500">
            Import pipeline: profile → map → clean → index → resolve. Runs asynchronously on Celery.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <Badge tone={active.length ? "sky" : "emerald"}>{active.length} active</Badge>
          <Badge tone={failed ? "rose" : "slate"}>{failed} failed</Badge>
          <button onClick={load} className="rounded-lg border border-slate-200 px-2.5 py-1 text-slate-500 hover:bg-slate-50">
            refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
      )}

      {loading && jobs.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-24">
          <Spinner className="h-5 w-5" />
          <span className="text-sm text-slate-500">Loading jobs…</span>
        </div>
      ) : jobs.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No jobs yet"
          hint="Upload a file on the Import page or seed the demo datasets to kick off the pipeline."
        />
      ) : (
        <div className="space-y-3">
          {jobs.map((job) => {
            const open = openId === job.id;
            const done = job.status === "Completed";
            const isFailed = job.status === "Failed";
            const stage = STAGE.find((s) => s.regex.test(job.status))?.label || job.status;
            return (
              <div
                key={job.id}
                className={`overflow-hidden rounded-2xl border bg-white transition ${
                  isFailed ? "border-rose-200" : open ? "border-accent-300 shadow-card" : "border-slate-200 shadow-card"
                }`}
              >
                <button onClick={() => toggle(job.id)} className="flex w-full flex-wrap items-center gap-3 px-5 py-3.5 text-left hover:bg-slate-50/60">
                  <div
                    className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${
                      done ? "bg-emerald-100 text-emerald-600" : isFailed ? "bg-rose-100 text-rose-600" : "bg-sky-100 text-sky-600"
                    }`}
                  >
                    {done ? <CheckCircle2 className="h-5 w-5" /> : isFailed ? <XCircle className="h-5 w-5" /> : <Spinner className="h-5 w-5" />}
                  </div>

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-semibold text-ink-900">{job.filename || `#${job.id}`}</span>
                      <Badge tone={statusTone(job.status)}>{job.status}</Badge>
                      {!done && !isFailed && <Badge tone="sky">{stage}</Badge>}
                    </div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-slate-400">
                      <span>source #{job.source_id}</span>
                      <span>{job.file_type}</span>
                      <span>{fmtBytes(job.file_size)}</span>
                      <span>{job.total_rows} rows</span>
                      {job.started_at && (
                        <span className="inline-flex items-center gap-1">
                          <Timer className="h-3 w-3" />
                          {job.completed_at ? `${((new Date(job.completed_at) - new Date(job.started_at)) / 1000).toFixed(1)}s` : "running"}
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="shrink-0 text-right">
                    <div className="text-sm font-semibold tabular-nums text-ink-900">
                      {fmt(job.imported_rows)} <span className="text-[11px] font-normal text-slate-400">imported</span>
                    </div>
                    <div className="text-[11px] tabular-nums text-slate-400">
                      {fmt(job.dupe_rows)} dupes · {fmt(job.failed_rows)} failed
                    </div>
                  </div>
                  <ChevronDown className={`h-4 w-4 text-slate-400 transition ${open ? "rotate-180" : ""}`} />
                </button>

                {job.status === "PendingConfirmation" && (
                  <div className="flex flex-wrap items-center gap-2 border-t border-amber-200 bg-amber-50/60 px-5 py-2.5">
                    <span className="text-xs text-amber-800">Mapping needs approval to continue processing.</span>
                    <button
                      onClick={() => confirm(job)}
                      disabled={confirming === job.id}
                      className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      {confirming === job.id ? <Spinner className="h-3.5 w-3.5" /> : <BadgeCheck className="h-3.5 w-3.5" />}
                      Confirm Mapping & Proceed
                    </button>
                  </div>
                )}

                {open && <JobDetail job={job} mapping={mappings[job.id]} onRetry={retry} onConfirm={() => confirm(job)} confirming={confirming === job.id} />}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function JobDetail({ job, mapping, onRetry, onConfirm, confirming }) {
  const lowConfidence = (mapping?.confidence ?? 1) < 0.5;
  return (
    <div className="space-y-4 border-t border-slate-100 bg-ink-50/40 px-5 py-4">
      <ProgressBar value={job.progress} label="Pipeline progress" />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Rows" value={job.total_rows} sub={`${job.processed_rows} processed`} />
        <Stat label="Imported" value={job.imported_rows} sub={`${job.chunks_completed}/${job.chunk_count} chunks`} />
        <Stat label="Duplicates" value={job.dupe_rows} sub="skipped on (dataset, hash)" />
        <Stat label="Failed" value={job.failed_rows} sub="reported as malformed" />
      </div>

      {job.error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-700">
          <span className="font-semibold">error:</span> {job.error}
        </div>
      )}

      {job.warnings?.length > 0 && (
        <div className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
          {job.warnings.map((w, i) => (
            <div key={i} className="text-xs text-amber-800">⚠ {w}</div>
          ))}
        </div>
      )}

      {mapping && (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            <span>Auto-detected mapping · confidence {Math.round((mapping.confidence ?? 0) * 100)}%</span>
            {lowConfidence && <span className="normal-case tracking-normal text-amber-600">low confidence — verify below</span>}
          </div>
          {mapping.tables?.map((t) => (
            <div key={t.table_name} className="mt-2 flex flex-wrap gap-1.5">
              <span className="text-xs font-semibold text-slate-600">{t.table_name}:</span>
              {Object.entries(t.mapping || {}).map(([ctype, col]) => (
                <span key={ctype} className="rounded-md bg-ink-50 px-2 py-0.5 font-mono text-[11px] text-slate-600">
                  <span className="text-grape">{ctype}</span> ← {col}
                </span>
              ))}
              {Object.keys(t.mapping || {}).length === 0 && (
                <span className="text-xs text-slate-400">no canonical mapping detected</span>
              )}
            </div>
          ))}
        </div>
      )}

      {job.stats && Object.keys(job.stats).length > 0 && (
        <details className="rounded-xl border border-slate-200 bg-white p-4">
          <summary className="cursor-pointer text-xs font-semibold text-slate-600">pipeline stats</summary>
          <pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-ink-950 p-3 font-mono text-[10px] leading-relaxed text-slate-300">
            {JSON.stringify(job.stats, null, 2)}
          </pre>
        </details>
      )}

      {lowConfidence && !["Completed", "Failed"].includes(job.status) && (
        <button
          onClick={onConfirm}
          disabled={confirming}
          className="inline-flex items-center gap-1.5 rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {confirming ? <Spinner className="h-3.5 w-3.5" /> : <BadgeCheck className="h-3.5 w-3.5" />}
          Confirm Mapping & Proceed
        </button>
      )}

      {letActive(job, onRetry)}
    </div>
  );
}

function letActive(job, onRetry) {
  const running = !["Completed", "Failed"].includes(job.status);
  const isFailed = job.status === "Failed";
  if (running) {
    return (
      <div className="flex items-center gap-2 rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-700">
        <Spinner className="h-3.5 w-3.5" /> Job is processing asynchronously — this panel auto-refreshes.
      </div>
    );
  }
  if (isFailed) {
    return (
      <button
        onClick={() => onRetry(job.id)}
        className="flex items-center gap-2 rounded-lg bg-ink-800 px-3 py-2 text-xs font-medium text-white hover:bg-ink-700"
      >
        <RotateCcw className="h-3.5 w-3.5" /> Retry import
      </button>
    );
  }
  return (
    <div className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
      Completed in {job.completed_at && job.started_at
        ? `${((new Date(job.completed_at) - new Date(job.started_at)) / 1000).toFixed(1)}s`
        : "—"}.
    </div>
  );
}

function Stat({ label, value, sub }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2.5">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-lg font-bold tabular-nums text-ink-900">{fmt(value)}</div>
      {sub && <div className="text-[10px] text-slate-400">{sub}</div>}
    </div>
  );
}

function fmt(n) {
  if (n == null) return "0";
  return new Intl.NumberFormat().format(n);
}

function fmtBytes(b) {
  if (b == null) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}