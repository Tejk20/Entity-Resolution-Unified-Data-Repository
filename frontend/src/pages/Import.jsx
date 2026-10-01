import {
  Check,
  ChevronRight,
  CloudUpload,
  Files,
  FlaskConical,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Badge, ProgressBar, Spinner, statusTone } from "../components/ui";
import { apiV1 } from "../lib/api";

const CANONICAL = [
  "email", "phone", "name", "username", "member_id", "address", "company",
];

export default function Import() {
  const inputRef = useRef(null);
  const [mode, setMode] = useState("upload"); // upload | preview | running | done
  const [sources, setSources] = useState([]);
  const [multipartSource, setMultipartSource] = useState("");
  const [job, setJob] = useState(null);
  const [preview, setPreview] = useState(null);
  const [edits, setEdits] = useState({});
  const [seedOpen, setSeedOpen] = useState(false);
  const [seedInfo, setSeedInfo] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [deleting, setDeleting] = useState(null);

  async function removeSource(id) {
    const name = (sources.find((s) => s.id === id) || {}).name || `#${id}`;
    if (!window.confirm(`Delete dataset "${name}"? Its linked records, entities and jobs will be removed.`)) return;
    setDeleting(id);
    try {
      await apiV1.deleteSource(id);
      const fresh = await apiV1.sources().catch(() => []);
      setSources(fresh);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setDeleting(null);
    }
  }

  useEffect(() => {
    apiV1.sources().then(setSources).catch(() => {});
    apiV1.seedSummary().then((s) => setSeedInfo((s.datasets || []).concat(s.extra || []))).catch(() => {});
  }, []);

const jobIdRef = useRef(null);
  jobIdRef.current = job?.id ?? null;

  // live progress poller
  useEffect(() => {
    const jobId = jobIdRef.current;
    if (mode !== "running" || !jobId) return;
    const t = setInterval(async () => {
      try {
        const fresh = await apiV1.job(jobId);
        setJob(fresh);
        if (["Completed", "Failed"].includes(fresh.status)) {
          setMode("done");
          setPreview((p) => ({ ...p, selectedJob: fresh }));
        }
      } catch {
        /* ignore transient */
      }
    }, 1500);
    return () => clearInterval(t);
  }, [mode, jobIdRef]);

  async function onPick(e) {
    const f = e.target.files?.[0];
    if (!f) return;
    setError(null);
    try {
      const res = await apiV1.upload(f, {
        name: f.name,
        source_id: multipartSource || undefined,
      });
      setJob({ id: res.job_id, uid: res.job_uid, filename: res.filename, status: res.status });
      setMode("preview");
    } catch (err) {
      setError(err.message);
    }
  }

  function applyEdit(table, col, ctype) {
    setEdits((prev) => {
      const next = { ...prev };
      next[table] = { ...(next[table] || {}) };
      next[table][typeof col === "string" ? col : col] = ctype === "__none__" ? null : ctype;
      return next;
    });
  }

  async function confirm() {
    if (!preview || !job) return;
    setBusy(true);
    setError(null);
    try {
      const tableMappings = {};
      const defaultMapping = { ...(preview.tables?.[0]?.mapping || {}) };
      for (const t of preview.tables || []) {
        tableMappings[t.table_name] = { ...(t.mapping || {}) };
      }
      // apply operator edits: edits[table][canonical] = selected column
      for (const [table, cols] of Object.entries(edits)) {
        const tm = tableMappings[table];
        for (const [canonical, column] of Object.entries(cols)) {
          if (tm && column && tm[canonical] !== column) {
            for (const [k, v] of Object.entries(tm)) if (v === column) delete tm[k];
          }
          if (tm && canonical) {
            if (column) tm[canonical] = column;
            else delete tm[canonical];
          }
        }
      }
      const primary = preview.tables?.[0]?.table_name;
      const mapping = Object.fromEntries(
        Object.entries(tableMappings[primary] || defaultMapping).filter(([, v]) => v)
      );
      await apiV1.confirmMapping(job.id, { mapping, table_mappings: tableMappings });
      setBusy(false);
      setMode("running");
      const fresh = await apiV1.job(job.id);
      setJob(fresh);
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  async function seedDemo() {
    setBusy(true);
    setError(null);
    try {
      await apiV1.seed("csv");
      setBusy(false);
      window.location.href = "/jobs";
    } catch (e) {
      setBusy(false);
      setError(e.message);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-ink-900">Import Data</h1>
        <p className="text-sm text-slate-500">
          Upload CSV, TSV or SQL dumps. Rows are cleaned in chunks, indexed by identifier,
          and clustered into master entities — all asynchronously.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      {/* -------------------- manage datasets -------------------- */}
      {sources.length > 0 && (
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink-900">Imported Datasets</h2>
            <span className="text-[11px] text-slate-400">{sources.length} source{sources.length === 1 ? "" : "s"}</span>
          </div>
          <ul className="divide-y divide-slate-100">
            {sources.map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-3 py-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    {s.system_key && (
                      <span className="grid h-5 w-5 shrink-0 place-items-center rounded bg-ink-800 text-[10px] font-bold text-white">
                        {s.system_key}
                      </span>
                    )}
                    <span className="truncate text-sm font-medium text-slate-800">{s.name}</span>
                    <Badge tone="slate">{s.source_type}</Badge>
                  </div>
                  <div className="mt-0.5 truncate text-xs text-slate-500">{s.description}</div>
                </div>
                <button
                  onClick={() => removeSource(s.id)}
                  disabled={deleting === s.id}
                  title={`Delete dataset ${s.name}`}
                  className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ------------------------- upload stage ------------------------- */}
      {mode === "upload" && (
        <>
          <div className="flex flex-wrap items-end gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-card">
            <div className="flex-1 min-w-[220px]">
              <label className="text-xs font-semibold text-slate-600 ">Existing source (multi-part import)</label>
              <select
                value={multipartSource}
                onChange={(e) => setMultipartSource(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
              >
                <option value="">New dataset / source</option>
                {sources.map((s) => (
                  <option key={s.id} value={s.id}>
                    #{s.id} — {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="text-xs text-slate-400">
              Picking an existing source appends rows to it; duplicates are skipped.
            </div>
          </div>

          <button
            onClick={() => inputRef.current?.click()}
            className="flex w-full flex-col items-center gap-4 rounded-2xl border-2 border-dashed border-slate-300 bg-white/70 py-14 transition hover:border-accent-400 hover:bg-accent-50/40"
          >
            <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-accent-500 to-grape text-white shadow-lift">
              <CloudUpload className="h-7 w-7" />
            </div>
            <div>
              <div className="text-sm font-semibold text-ink-900">Drop a file here or click to browse</div>
              <div className="mt-1 text-xs text-slate-500">
                .csv · .tsv · .sql (INSERT or COPY dumps) · up to 2 GB
              </div>
            </div>
          </button>
          <input ref={inputRef} type="file" className="hidden" onChange={onPick} />

          {/* demo seeding */}
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
            <div className="flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-sm font-semibold text-ink-900">
                <FlaskConical className="h-4 w-4 text-grape" /> Demo datasets (A · B · C · D)
              </h2>
              <button
                onClick={() => setSeedOpen((v) => !v)}
                className="text-xs font-medium text-accent-600 hover:underline"
              >
                {seedOpen ? "hide" : "preview"}
              </button>
            </div>
            {seedOpen && (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {seedInfo.map((s) => (
                  <div key={s.system_key} className="rounded-lg border border-slate-200 bg-ink-50 p-3">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-ink-900">{s.system_key} · {s.label}</span>
                      <Badge tone="slate">{s.rows} rows</Badge>
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500">{s.description}</div>
                    <div className="mt-1.5 font-mono text-[10px] tracking-tight text-slate-400">
                      {(s.columns || (s.format ? [s.format] : [])).join(" · ")}
                    </div>
                  </div>
                ))}
                <button
                  onClick={seedDemo}
                  disabled={busy}
                  className="flex items-center justify-center gap-2 rounded-lg bg-ink-800 px-4 py-3 text-sm font-medium text-white hover:bg-ink-700 disabled:opacity-50"
                >
                  {busy ? <Spinner className="h-4 w-4 text-white" /> : <Sparkles className="h-4 w-4" />}
                  Seed all via ingestion pipeline
                </button>
              </div>
            )}
          </section>
        </>
      )}

      {/* ----------------------- mapping preview ------------------------ */}
      {mode === "preview" && preview && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="grid h-10 w-10 place-items-center rounded-lg bg-sky-50 text-sky-600">
                <Files className="h-5 w-5" />
              </div>
              <div>
                <div className="text-sm font-semibold text-ink-900">{job?.filename}</div>
                <div className="text-xs text-slate-500">
                  {preview.total_rows ?? 0} rows · {preview.chunk_count ?? "?"} chunks of {preview.chunk_size ?? "?"} ·
                  confidence {Math.round((preview.confidence ?? 0) * 100)}%
                </div>
              </div>
            </div>
            <button
              onClick={confirm}
              disabled={busy}
              className="flex items-center gap-2 rounded-lg bg-accent-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-accent-500 disabled:opacity-60"
            >
              {busy ? <Spinner className="h-4 w-4 text-white" /> : <Check className="h-4 w-4" />}
              Confirm mapping & run
            </button>
          </div>

          {preview.warnings?.length > 0 && (
            <div className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
              {preview.warnings.map((w, i) => (
                <div key={i} className="text-xs text-amber-800">⚠ {w}</div>
              ))}
            </div>
          )}

          {(preview.tables || []).map((table) => (
            <MappingTable
              key={table.table_name}
              table={table}
              edits={edits[table.table_name] || {}}
              onEdit={(col, ctype) => applyEdit(table.table_name, col, ctype)}
            />
          ))}
        </div>
      )}

      {/* ------------------------- running ------------------------------ */}
      {mode === "running" && job && (
        <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-card">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Spinner />
              <div>
                <div className="text-sm font-semibold text-ink-900">
                  Importing {job.filename}
                </div>
                <div className="text-xs text-slate-500">
                  {job.status} · {job.imported_rows ?? 0} imported · {job.dupe_rows ?? 0} duplicates skipped
                </div>
              </div>
            </div>
            <Badge tone={statusTone(job.status)}>{job.status}</Badge>
          </div>
          <ProgressBar value={job.progress} label="Overall progress" />
          <div className="mt-5 grid grid-cols-3 gap-4 text-center">
            <Phase label="Mapping" on={job.status === "Mapping"} done={preview?.confidence != null} />
            <Phase label="Cleaning" on={["Cleaning", "Indexing", "Matching", "Completed"].includes(job.status)} done={["Indexing", "Matching", "Completed"].includes(job.status)} />
            <Phase label="Matching" on={["Matching", "Completed"].includes(job.status)} done={job.status === "Completed"} />
          </div>
        </div>
      )}

      {/* --------------------------- done ------------------------------- */}
      {mode === "done" && job && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-6">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-full bg-emerald-600 text-white">
              <Check className="h-5 w-5" />
            </div>
            <div className="flex-1">
              <div className="text-sm font-semibold text-emerald-900">
                {job.status === "Completed" ? "Import completed" : "Import failed"}
              </div>
              <div className="text-xs text-emerald-800">
                {job.imported_rows} records imported · {job.dupe_rows} skipped ·{" "}
                {job.stats?.invalid_cells ?? 0} malformed cells reported
                {job.stats?.resolution?.entities_created != null && (
                  <> · {job.stats.resolution.clusters} clusters, {job.stats.resolution.entities_created} new entities</>
                )}
              </div>
            </div>
            <LinkButton to="/search">Search now</LinkButton>
          </div>
          {job.warnings?.length > 0 && (
            <div className="mt-3 rounded-lg bg-white/70 px-3 py-2 text-xs text-amber-700">
              {job.warnings.join(" · ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MappingTable({ table, edits, onEdit }) {
  const [open, setOpen] = useState(true);
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-3.5 hover:bg-slate-50"
      >
        <div className="flex items-center gap-2 text-sm font-semibold text-ink-900">
          <ChevronRight className={`h-4 w-4 transition ${open ? "rotate-90" : ""}`} />
          {table.table_name}
          <Badge tone="sky">confidence {Math.round(table.confidence * 100)}%</Badge>
        </div>
      </button>

      {open && (
        <div className="border-t border-slate-100 px-5 py-4">
          <div className="grid gap-3 lg:grid-cols-2">
            {/* mapping editor: canonical field -> column */}
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 bg-slate-50 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Canonical mapping
              </div>
              <div className="divide-y divide-slate-100">
                {CANONICAL.map((ctype) => {
                  const auto = table.mapping[ctype];
                  const chosen = edits[ctype] ?? (edits[ctype] === null ? "" : auto ?? "");
                  return (
                    <div key={ctype} className="flex items-center gap-2 px-3 py-2">
                      <span className="w-24 shrink-0 text-xs font-medium text-slate-600">
                        {ctype}
                      </span>
                      <select
                        value={chosen ?? ""}
                        onChange={(e) => onEdit(ctype, e.target.value)}
                        className="flex-1 rounded-md border border-slate-200 px-2 py-1.5 text-xs"
                      >
                        <option value="">— none —</option>
                        {rowKeys(table).map((col) => (
                          <option key={col} value={col}>
                            {col}
                          </option>
                        ))}
                      </select>
                      {table.columns.find((c) => c.column === (chosen ?? auto))?.inferred_type === ctype && (
                        <Badge tone="emerald">✓</Badge>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>

            {/* column suggestions with confidence + reasons */}
            <div className="rounded-lg border border-slate-200">
              <div className="border-b border-slate-100 bg-slate-50 px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                Detected columns · suggestions
              </div>
              <div className="max-h-72 divide-y divide-slate-100 overflow-y-auto">
                {[...table.columns]
                  .sort((a, b) => (b.suggestions?.[0]?.confidence ?? 0) - (a.suggestions?.[0]?.confidence ?? 0))
                  .map((col) => (
                    <div key={col.column} className="px-3 py-2">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-xs font-medium text-slate-700">{col.column}</span>
                        <div className="flex items-center gap-1">
                          <Badge tone="slate">{col.dtype}</Badge>
                          {col.inferred_type && <Badge tone="violet">{col.inferred_type}</Badge>}
                        </div>
                      </div>
                      <div className="mt-1 space-y-0.5">
                        {col.suggestions.slice(0, 3).map((s) => (
                          <div key={s.canonical_type} className="flex items-center justify-between text-[11px]">
                            <span className="text-slate-500">{s.reason}</span>
                            <span className="ml-2 shrink-0 font-mono tabular-nums text-slate-400">
                              {s.canonical_type} {Math.round(s.confidence * 100)}%
                            </span>
                          </div>
                        ))}
                      </div>
                      {col.uniqueness > 0 && (
                        <div className="mt-1 text-[10px] text-slate-400">
                          {col.uniqueness * 100}% unique · {col.null_ratio * 100}% null
                        </div>
                      )}
                    </div>
                  ))}
              </div>
            </div>
          </div>

          {/* sample rows */}
          <details className="mt-3">
            <summary className="cursor-pointer text-xs font-medium text-slate-500 hover:text-slate-700">
              raw sample rows
            </summary>
            <div className="mt-2 overflow-x-auto rounded-lg border border-slate-200">
              <table className="min-w-full text-xs">
                <thead className="bg-slate-50 text-left text-slate-500">
                  <tr>
                    {rowKeys(table).map((k) => (
                      <th key={k} className="px-3 py-1.5 font-medium">{k}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {table.sample_rows?.map((r, i) => (
                    <tr key={i}>
                      {rowKeys(table).map((k) => (
                        <td key={k} className="px-3 py-1.5 font-mono text-slate-600">
                          {String(r[k] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </div>
      )}
    </section>
  );
}

function rowKeys(table) {
  return table.sample_rows?.[0]
    ? Object.keys(table.sample_rows[0]).filter((k) => k !== "__row__")
    : (table.columns || []).map((c) => c.column);
}

function Phase({ label, on, done }) {
  return (
    <div className="flex flex-col items-center gap-1.5">
      <div
        className={`grid h-8 w-8 place-items-center rounded-full text-xs font-bold ${
          done ? "bg-emerald-600 text-white" : on ? "bg-accent-500 text-white" : "bg-slate-100 text-slate-400"
        }`}
      >
        {done ? <Check className="h-4 w-4" /> : on ? <Spinner className="h-4 w-4" /> : <X className="h-4 w-4" />}
      </div>
      <span className="text-[11px] text-slate-500">{label}</span>
    </div>
  );
}

function LinkButton({ to, children }) {
  return (
    <Link to={to} className="rounded-lg bg-accent-600 px-4 py-2 text-xs font-medium text-white hover:bg-accent-500">
      {children}
    </Link>
  );
}