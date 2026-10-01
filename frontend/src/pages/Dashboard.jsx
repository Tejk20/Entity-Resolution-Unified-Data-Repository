import { Activity, Clock, Database, Files, GitCommit, GitMerge, Layers, Trash2, Users } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Badge, EmptyState, StatCard } from "../components/ui";
import { apiV1 } from "../lib/api";

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [sources, setSources] = useState([]);
  const [breakdown, setBreakdown] = useState(null);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [deleting, setDeleting] = useState(null);

  async function removeSource(id) {
    const name = (sources.find((s) => s.id === id) || {}).name || `#${id}`;
    if (!window.confirm(`Delete dataset "${name}"? Its linked records, entities and jobs will be removed.`)) return;
    setDeleting(id);
    try {
      await apiV1.deleteSource(id);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setDeleting(null);
    }
  }

  const load = useCallback(async () => {
    try {
      const [s, src, bd, h] = await Promise.all([
        apiV1.stats(),
        apiV1.sources(),
        apiV1.statsBreakdown(),
        apiV1.health().catch(() => null),
      ]);
      setStats(s);
      setSources(src);
      setBreakdown(bd);
      setHealth(h);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-ink-900">Repository Overview</h1>
          <p className="text-sm text-slate-500">
            Live status across all imported datasets and resolved entities.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {(["database", "redis", "worker"]).map((k) => (
            <Badge
              key={k}
              tone={health?.[k] === "up" ? "emerald" : "rose"}
            >
              <span className={`h-1.5 w-1.5 rounded-full ${health?.[k] === "up" ? "bg-emerald-500" : "bg-rose-500"}`} />
              {k} {health?.[k] === "up" ? "up" : "down"}
            </Badge>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          Backend unreachable: {error}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Datasets" value={stats?.total_datasets ?? "—"} sub={`${stats?.total_sources ?? 0} sources`} icon={Layers} tone="sky" />
        <StatCard label="Total Records" value={format(stats?.total_records)} sub={`${format(stats?.identifiers ?? stats?.total_identifiers)} identifiers indexed`} icon={Files} tone="grape" />
        <StatCard label="Master Entities" value={format(stats?.total_entities)} sub="unified profiles" icon={GitMerge} tone="mint" />
        <StatCard label="Matched Entities" value={format(stats?.matched_entities)} sub={`${format(stats?.linked_records)} records linked`} icon={Users} tone="amber" />
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Processing Failures" value={format(stats?.processing_failures ?? 0)} sub={`${stats?.failed_jobs ?? 0} failed jobs`} icon={Activity} tone="coral" />
        <StatCard label="Active Jobs" value={stats?.active_jobs ?? 0} sub="running now" icon={Clock} tone="sky" />
        <StatCard label="Duplicates Skipped" value={format(stats?.duplicate_rows_skipped ?? 0)} sub="deduped on (dataset, hash)" icon={GitCommit} tone="slate" />
        <StatCard
          label="Avg Exec / Job"
          value={`${stats?.execution_time?.avg_seconds ?? "—"}s`}
          sub={`max ${stats?.execution_time?.max_seconds ?? 0}s · ${stats?.search?.total_runs ?? 0} searches`}
          icon={Clock}
          tone="grape"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold text-ink-900">Imported Datasets</h2>
            <Link to="/import" className="text-xs font-medium text-accent-600 hover:underline">
              Import new →
            </Link>
          </div>
          {sources.length === 0 ? (
            <EmptyState icon={Database} title="No datasets yet" hint="Upload a CSV or SQL dump, or seed the demo datasets." />
          ) : (
            <ul className="divide-y divide-slate-100">
              {sources.map((s) => {
                const info = (breakdown?.sources || []).find((b) => b.source_id === s.id);
                return (
                  <li key={s.id} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        {s.system_key && (
                          <span className="grid h-5 w-5 shrink-0 place-items-center rounded bg-ink-800 text-[10px] font-bold text-white">
                            {s.system_key}
                          </span>
                        )}
                        <div className="truncate text-sm font-medium text-slate-800">{s.name}</div>
                        <Badge tone="slate">{s.source_type}</Badge>
                      </div>
                      <div className="mt-0.5 truncate text-xs text-slate-500">{s.description}</div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <div className="text-right">
                        <div className="text-sm font-semibold tabular-nums text-ink-900">{format(info?.records ?? s.imported_rows)}</div>
                        <div className="text-[11px] text-slate-400">{info?.entities ?? 0} entities</div>
                      </div>
                      <button
                        onClick={() => removeSource(s.id)}
                        disabled={deleting === s.id}
                        title={`Delete dataset ${s.name}`}
                        className="grid h-6 w-6 place-items-center rounded-md text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-card">
          <h2 className="mb-3 text-sm font-semibold text-ink-900">Identifier Index</h2>
          {breakdown?.identifier_types?.length ? (
            <div className="space-y-2">
              {breakdown.identifier_types.map((t) => {
                const total = breakdown.identifier_types.reduce((a, b) => a + b.count, 0) || 1;
                const pct = (t.count / total) * 100;
                return (
                  <div key={t.type} className="flex items-center gap-3">
                    <span className="w-24 text-xs font-medium text-slate-600">{t.type}</span>
                    <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
                      <div className="h-full rounded-full bg-gradient-to-r from-accent-400 to-grape" style={{ width: `${pct}%` }} />
                    </div>
                    <span className="w-16 text-right text-xs tabular-nums text-slate-500">
                      {format(t.count)} / {format(t.distinct_values)}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : (
            <EmptyState icon={Activity} title="No identifiers indexed" hint="Import data or run a resolution search to populate the index." />
          )}
          <div className="mt-4 rounded-lg bg-ink-50 p-3 text-xs text-slate-600">
            <span className="font-semibold text-ink-800">Demo:</span> try searching{" "}
            <Link to="/search" className="font-mono font-semibold text-accent-600 hover:underline">
              john@example.com
            </Link>{" "}
            to watch a 4-hop progressive enrichment across all four datasets.
          </div>
        </section>
      </div>
    </div>
  );
}

function format(n) {
  if (n == null) return "0";
  return new Intl.NumberFormat().format(n);
}