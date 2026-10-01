import {
  ArrowLeft,
  Braces,
  Building2,
  GitBranch,
  GitMerge,
  Mail,
  MapPin,
  Phone,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Badge, EmptyState, Spinner, statusTone } from "../components/ui";
import { apiV1 } from "../lib/api";

const TYPE_ICONS = {
  email: Mail,
  phone: Phone,
  username: Users,
  member_id: GitMerge,
  name: Users,
  address: MapPin,
  company: Building2,
};

export default function EntityDetail() {
  const { id } = useParams();
  const [view, setView] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [v, tl] = await Promise.all([
        apiV1.entity(id),
        apiV1.entityTimeline(id).catch(() => null),
      ]);
      setView(v);
      setTimeline(tl);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading && !view) {
    return (
      <div className="flex items-center justify-center gap-2 py-24">
        <Spinner className="h-5 w-5" />
        <span className="text-sm text-slate-500">Loading entity…</span>
      </div>
    );
  }

  if (error || !view) {
    return (
      <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
        {error || "Entity not found"}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <Link to="/entities" className="inline-flex items-center gap-1.5 text-xs font-medium text-accent-600 hover:underline">
        <ArrowLeft className="h-3.5 w-3.5" /> Master entities
      </Link>

      {/* header */}
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-4">
            <div className="grid h-14 w-14 place-items-center rounded-2xl bg-gradient-to-br from-accent-500 to-grape text-white shadow-lift">
              <Users className="h-7 w-7" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-ink-900">
                {view.display_name || view.primary_email || view.entity_key}
              </h1>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
                <span className="font-mono">{view.entity_key}</span>
                {view.primary_email && (
                  <span className="inline-flex items-center gap-1"><Mail className="h-3 w-3" /> {view.primary_email}</span>
                )}
                {view.primary_phone && (
                  <span className="inline-flex items-center gap-1"><Phone className="h-3 w-3" /> {view.primary_phone}</span>
                )}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone={statusTone(view.status)}>{view.status}</Badge>
            <Badge tone="sky">{Math.round(view.confidence * 100)}% confidence</Badge>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <MiniStat label="Records" value={view.record_count} />
          <MiniStat label="Sources" value={view.source_count} />
          <MiniStat label="Identifiers" value={view.identifier_count} />
          <MiniStat
            label="Last seen"
            value={view.last_seen ? new Date(view.last_seen).toLocaleDateString() : "—"}
          />
        </div>

        {view.attributes && Object.keys(view.attributes).length > 0 && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {Object.entries(view.attributes).map(([k, v]) => (
              <span key={k} className="rounded-md bg-ink-50 px-2 py-1 text-[11px] text-slate-600">
                <span className="text-slate-400">{k}:</span>{" "}
                <span className="font-medium">{String(v)}</span>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        {/* identifiers */}
        <section className="space-y-4">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink-900">
              <GitMerge className="h-4 w-4 text-grape" /> Identifier set
            </h2>
            {Object.keys(view.identifiers || {}).length === 0 ? (
              <p className="text-xs text-slate-400">No identifiers indexed.</p>
            ) : (
              <div className="space-y-3">
                {Object.entries(view.identifiers).map(([type, idents]) => {
                  const IconC = TYPE_ICONS[type] || GitMerge;
                  return (
                    <div key={type}>
                      <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                        <IconC className="h-3.5 w-3.5" /> {type} · {idents.length}
                      </div>
                      <div className="space-y-1">
                        {idents.map((i) => (
                          <div
                            key={`${i.value}:${i.raw}`}
                            className="flex items-center justify-between rounded-lg bg-ink-50 px-2.5 py-1.5 font-mono text-[11px]"
                          >
                            <span className="truncate text-slate-700" title={i.raw || i.value}>
                              {i.value}
                            </span>
                            <span className="ml-2 shrink-0 tabular-nums text-slate-400">×{i.record_count}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card">
            <h2 className="mb-3 text-sm font-semibold text-ink-900">Sources feeding this entity</h2>
            {Object.keys(view.source_breakdown || {}).length === 0 ? (
              <p className="text-xs text-slate-400">None.</p>
            ) : (
              <div className="space-y-1.5">
                {Object.entries(view.source_breakdown).map(([name, count]) => (
                  <div key={name} className="flex items-center justify-between text-xs">
                    <span className="truncate font-mono text-slate-600">{name}</span>
                    <span className="tabular-nums text-slate-400">{count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* hop timeline with raw/clean records */}
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card">
          <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-ink-900">
            <GitBranch className="h-4 w-4 text-grape" /> Discovery timeline
          </h2>
          <RecordsTimeline timeline={timeline} onRefresh={load} />
        </section>
      </div>
    </div>
  );
}

function MiniStat({ label, value }) {
  return (
    <div className="rounded-xl border border-slate-100 bg-ink-50 px-3 py-2.5">
      <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
      <div className="text-lg font-bold tabular-nums text-ink-900">{value}</div>
    </div>
  );
}

function RecordsTimeline({ timeline, onRefresh }) {
  if (!timeline) {
    return (
      <button
        onClick={onRefresh}
        className="w-full rounded-xl border border-dashed border-slate-300 py-8 text-xs text-slate-400 hover:text-slate-600"
      >
        timeline unavailable — click to reload
      </button>
    );
  }

  const hops = timeline.hops || [];
  if (hops.length === 0) {
    return <EmptyState icon={GitBranch} title="No discovery timeline" hint="This entity has no linked records yet." />;
  }

  return (
    <div className="space-y-4">
      {hops.map((hop) => (
        <HopGroup key={hop.hop} hop={hop} />
      ))}
    </div>
  );
}

function HopGroup({ hop }) {
  const [panel, setPanel] = useState(false);
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <span
          className={`grid h-6 w-6 place-items-center rounded-full text-[10px] font-bold text-white ${
            hop.hop === 0 ? "bg-accent-500" : "bg-grape"
          }`}
        >
          {hop.hop}
        </span>
        <span className="text-xs font-semibold text-ink-900">{hop.label}</span>
        <span className="text-[11px] text-slate-400">
          {hop.records.length} records · {hop.sources.join(", ")}
        </span>
      </div>

      <div className="space-y-2 border-l border-slate-100 pl-4">
        {hop.records.map((r, i) => {
          const ml = r.matched_on;
          return (
            <div key={r.source_record_id ?? i} className="rounded-xl border border-slate-200 bg-ink-50/60">
              <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold text-ink-800">{r.source_name}</span>
                    {r.system_key && <Badge tone="slate">{r.system_key}</Badge>}
                    <span className="truncate text-[11px] text-slate-500">
                      {r.dataset_name ? `${r.dataset_name} · ` : ""}#{r.source_record_id}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {r.clean?.email && (
                    <span className="hidden items-center gap-1 text-[11px] text-slate-400 md:inline-flex">
                      <Mail className="h-3 w-3" /> {r.clean.email}
                    </span>
                  )}
                  {ml && (
                    <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500" title={`matched on ${ml.value}`}>
                      {ml.type}: {ml.value}
                    </span>
                  )}
                  <button
                    onClick={() => setPanel((v) => !v)}
                    className="rounded-md border border-slate-200 px-2 py-0.5 text-[10px] font-medium text-slate-500 hover:bg-white"
                  >
                    {panel ? "hide payloads" : "payloads"}
                  </button>
                </div>
              </div>

              {panel && (
                <div className="grid gap-2 border-t border-slate-200 bg-white px-3 py-2.5 md:grid-cols-2">
                  <Payload title="Raw" data={r.raw} />
                  <Payload title="Normalized" data={r.clean} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Payload({ title, data }) {
  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
        <Braces className="h-3 w-3" /> {title}
      </div>
      <pre className="max-h-48 overflow-auto rounded-lg bg-ink-950 p-2 font-mono text-[10px] leading-relaxed text-slate-300">
        {JSON.stringify(data, null, 2) || "—"}
      </pre>
    </div>
  );
}