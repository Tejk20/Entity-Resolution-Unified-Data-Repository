import { ChevronLeft, ChevronRight, GitMerge, Mail, Phone, Search, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { Badge, EmptyState, Spinner } from "../components/ui";
import { apiV1 } from "../lib/api";

const SORTS = [
  { key: "records", label: "Most records" },
  { key: "sources", label: "Most sources" },
  { key: "confidence", label: "Confidence" },
  { key: "recent", label: "Recently seen" },
  { key: "name", label: "Name A–Z" },
];

export default function Entities() {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("records");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    apiV1
      .entities({ page, page_size: 25, sort, q: q || undefined })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [q, sort, page]);

  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / 25));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-ink-900">Master Entities</h1>
          <p className="text-sm text-slate-500">
            Unified profiles built by clustering equivalent identifiers across datasets.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
              placeholder="filter by name / email / key…"
              className="w-72 rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-accent-300"
            />
          </div>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value)}
            className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm focus:outline-none"
          >
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
      )}

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-card">
        {loading && !data ? (
          <div className="flex items-center justify-center gap-2 py-16">
            <Spinner className="h-5 w-5" /> <span className="text-sm text-slate-500">Loading entities…</span>
          </div>
        ) : data?.items?.length === 0 ? (
          <EmptyState
            icon={GitMerge}
            title="No master entities yet"
            hint={q ? "No entities match that filter." : "Import data or run a resolution search to build entities."}
          />
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-5 py-3 font-semibold">Entity</th>
                <th className="px-4 py-3 font-semibold">Confidence</th>
                <th className="px-4 py-3 font-semibold">Sources</th>
                <th className="px-4 py-3 font-semibold">Records</th>
                <th className="px-4 py-3 font-semibold">Identifiers</th>
                <th className="px-5 py-3 text-right font-semibold">Last seen</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {data.items.map((e) => (
                <tr key={e.id} className="hover:bg-accent-50/40">
                  <td className="px-5 py-3">
                    <Link to={`/entities/${e.id}`} className="group flex items-center gap-3">
                      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-gradient-to-br from-accent-500/20 to-grape/20 text-ink-800">
                        <Users className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <div className="truncate font-semibold text-ink-900 group-hover:text-accent-600">
                          {e.display_name || e.primary_email || e.entity_key}
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-slate-400">
                          <span className="font-mono">{e.entity_key}</span>
                          {e.primary_email && (
                            <span className="inline-flex items-center gap-1">
                              <Mail className="h-3 w-3" /> {e.primary_email}
                            </span>
                          )}
                          {e.primary_phone && (
                            <span className="inline-flex items-center gap-1">
                              <Phone className="h-3 w-3" /> {e.primary_phone}
                            </span>
                          )}
                        </div>
                      </div>
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-amber-400 to-emerald-500"
                          style={{ width: `${Math.round(e.confidence * 100)}%` }}
                        />
                      </div>
                      <span className="tabular-nums text-xs text-slate-500">{Math.round(e.confidence * 100)}%</span>
                    </div>
                  </td>
                  <td className="px-4 py-3 tabular-nums text-slate-700">{e.source_count}</td>
                  <td className="px-4 py-3">
                    <span className="font-semibold tabular-nums text-ink-900">{e.record_count}</span>
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone="violet">{e.identifier_count}</Badge>
                  </td>
                  <td className="px-5 py-3 text-right text-xs text-slate-400">
                    {e.last_seen ? new Date(e.last_seen).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!loading && data && (
        <div className="flex items-center justify-between text-sm">
          <span className="text-xs text-slate-500">
            {total.toLocaleString()} entities
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
              className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="px-3 text-xs text-slate-500">
              {page} / {pages}
            </span>
            <button
              onClick={() => setPage((p) => Math.min(pages, p + 1))}
              disabled={page >= pages}
              className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 disabled:opacity-40"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}