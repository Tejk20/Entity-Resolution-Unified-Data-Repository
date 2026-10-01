import {
  ArrowRight,
  ChevronDown,
  Database,
  GitBranch,
  GitMerge,
  History,
  Mail,
  Phone,
  Search as SearchIcon,
  Sparkles,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { Badge, EmptyState, Spinner, statusTone } from "../components/ui";
import { apiV1 } from "../lib/api";

const DEPTH_CHOICES = [
  { value: 2, label: "2 hops" },
  { value: 4, label: "4 hops" },
  { value: 8, label: "8 hops" },
];

const TYPE_ICONS = {
  email: Mail,
  phone: Phone,
  username: Users,
  member_id: GitMerge,
  name: Users,
};

export default function Search() {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState([]);
  const [inferred, setInferred] = useState(null);
  const [maxDepth, setMaxDepth] = useState(4);
  const [weak, setWeak] = useState(true);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [runs, setRuns] = useState([]);
  const [demoQuery, setDemoQuery] = useState("john@example.com");
  const debounce = useRef(null);

  useEffect(() => {
    apiV1
      .seedSummary()
      .then((s) => s.demo_query && setDemoQuery(s.demo_query))
      .catch(() => {});
    apiV1.recentRuns(8).then(setRuns).catch(() => {});
  }, []);

  const onType = useCallback((q) => {
    setQuery(q);
    setSuggestions([]);
    setInferred(null);
    if (!q || q.trim().length < 2) return;
    clearTimeout(debounce.current);
    debounce.current = setTimeout(async () => {
      try {
        const s = await apiV1.suggest(q);
        setSuggestions(s.suggestions || []);
        setInferred({ type: s.inferred_type, normalized: s.normalized });
      } catch {
        /* ignore */
      }
    }, 220);
  }, []);

  async function run(q, opts = {}) {
    const qText = (q ?? query).trim();
    if (!qText) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiV1.search({
        query: qText,
        max_depth: opts.depth ?? maxDepth,
        include_weak: opts.weak ?? weak,
        persist: true,
      });
      setResult(res);
      setQuery(qText);
      apiV1.recentRuns(8).then(setRuns).catch(() => {});
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-ink-900">Repository Search</h1>
        <p className="text-sm text-slate-500">
          Recursive, hop-by-hop enrichment from a single seed identifier across every dataset.
        </p>
      </div>

      {/* search bar */}
      <div className="relative rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex min-w-[280px] flex-1 items-center gap-2">
            <SearchIcon className="h-5 w-5 shrink-0 text-slate-400" />
            <input
              value={query}
              onChange={(e) => onType(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
              placeholder="email, phone, username, member id…"
              className="w-full bg-transparent text-sm text-ink-900 placeholder:text-slate-400 focus:outline-none"
            />
          </div>

          <div className="flex items-center gap-2">
            <select
              value={maxDepth}
              onChange={(e) => setMaxDepth(Number(e.target.value))}
              className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-600 focus:outline-none"
            >
              {DEPTH_CHOICES.map((d) => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
            <button
              onClick={() => setWeak((v) => !v)}
              className={`rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
                weak
                  ? "border-accent-300 bg-accent-50 text-accent-600"
                  : "border-slate-200 text-slate-500 hover:bg-slate-50"
              }`}
              title="Weak identifiers (name, address) extend the bridge across hops"
            >
              weak {weak ? "on" : "off"}
            </button>
            <button
              onClick={() => run()}
              disabled={loading || !query.trim()}
              className="flex items-center gap-2 rounded-lg bg-accent-600 px-5 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? <Spinner className="h-4 w-4 text-white" /> : <Sparkles className="h-4 w-4" />}
              Resolve
            </button>
          </div>
        </div>

        {inferred && query.trim().length >= 2 && (
          <div className="mt-2 text-xs text-slate-500">
            detected <span className="font-mono font-semibold text-grape">{inferred.type}</span>{" "}
            {inferred.normalized && (
              <>
                → normalized <span className="font-mono font-semibold text-ink-800">{inferred.normalized}</span>
              </>
            )}
          </div>
        )}

        {suggestions.length > 0 && (
          <ul className="mt-3 divide-y divide-slate-100 rounded-xl border border-slate-100 bg-white shadow-lift">
            {suggestions.map((s) => (
              <li key={`${s.type}:${s.value}`}>
                <button
                  onClick={() => {
                    onType(s.value);
                    run(s.value);
                    setSuggestions([]);
                  }}
                  className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left hover:bg-slate-50"
                >
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-sm text-ink-900">{s.value}</span>
                    <Badge tone="violet">{s.type}</Badge>
                  </div>
                  <span className="text-xs tabular-nums text-slate-400">{s.record_count} records</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* demo hint */}
      <button
        onClick={() => run(demoQuery)}
        className="flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-slate-300 bg-white/60 px-4 py-3 text-left text-xs text-slate-600 transition hover:border-accent-400 hover:bg-accent-50/40"
      >
        <GitBranch className="h-4 w-4 text-grape" />
        <span className="font-medium">Demo bridge:</span>
        <span className="font-mono font-semibold text-accent-600">{demoQuery}</span>
        <span className="text-slate-400">resolves across 4 datasets in 4 hops.</span>
        <ArrowRight className="ml-auto h-3.5 w-3.5 text-slate-400" />
      </button>

      {error && (
        <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-5">
          {result ? (
            <ResultView result={result} />
          ) : (
            <EmptyState
              icon={SearchIcon}
              title="No resolution yet"
              hint="Enter an identifier above — the resolver will trace records hop-by-hop and show the enrichment timeline."
            />
          )}
        </div>

        <aside className="space-y-5">
          <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink-900">
              <History className="h-4 w-4 text-slate-400" /> Recent searches
            </h2>
            {runs.length === 0 ? (
              <p className="text-xs text-slate-400">No runs yet. Seeding the demo data will change that.</p>
            ) : (
              <ul className="space-y-1">
                {runs.map((r) => (
                  <li key={r.run_uid}>
                    <button
                      onClick={() => run(r.query)}
                      className="group flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-left hover:bg-slate-50"
                    >
                      <div className="min-w-0">
                        <div className="truncate font-mono text-xs font-medium text-ink-800">{r.query}</div>
                        <div className="text-[10px] text-slate-400">
                          {r.hop_count} hops · {r.record_count} records · {r.source_count} sources
                        </div>
                      </div>
                      <Badge tone={statusTone(r.status)}>{r.status}</Badge>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {result && <BreakdownCard result={result} />}
        </aside>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- results -- */
function ResultView({ result }) {
  const [expandedHop, setExpandedHop] = useState(null);
  const entity = result.entity;

  return (
    <>
      {/* summary strip */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-card">
        <div className="flex items-center gap-3">
          <div
            className={`grid h-11 w-11 place-items-center rounded-xl ${
              result.found ? "bg-emerald-100 text-emerald-600" : "bg-slate-100 text-slate-400"
            }`}
          >
            {result.found ? <GitMerge className="h-6 w-6" /> : <Database className="h-6 w-6" />}
          </div>
          <div>
            <div className="text-sm font-bold text-ink-900">
              {result.found
                ? entity?.display_name || "Entity resolved"
                : "No matching records"}
            </div>
            <div className="text-xs text-slate-500">
              {result.found
                ? `${result.record_count} records · ${result.source_count} sources · ${result.hop_count} hops`
                : "The seed identifier produced an empty trace."}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {result.cached && <Badge tone="sky">cached</Badge>}
          <span className="tabular-nums text-slate-400">{result.duration_ms} ms</span>
          {entity?.id != null && (
            <Link
              to={`/entities/${entity.id}`}
              className="flex items-center gap-1 rounded-lg bg-ink-800 px-3 py-1.5 font-medium text-white hover:bg-ink-700"
            >
              view entity <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          )}
        </div>
      </div>

      {/* hop timeline */}
      {result.found && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-card">
          <h2 className="mb-4 flex items-center gap-2 text-sm font-semibold text-ink-900">
            <GitBranch className="h-4 w-4 text-grape" /> Progressive enrichment timeline
          </h2>
          <div className="relative">
            <div className="absolute bottom-3 left-4 top-3 w-px bg-gradient-to-b from-accent-400 via-grape/40 to-emerald-400/50" />
            <div className="space-y-3">
              {result.timeline.map((hop) => (
                <HopNode
                  key={hop.hop}
                  hop={hop}
                  open={expandedHop === hop.hop}
                  onToggle={() => setExpandedHop(expandedHop === hop.hop ? null : hop.hop)}
                />
              ))}
            </div>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {(result.identifiers || []).slice(0, 16).map((id) => {
              const IconC = TYPE_ICONS[id.type] || GitMerge;
              return (
                <span
                  key={`${id.type}:${id.value}`}
                  className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-ink-50 px-2.5 py-1 text-[11px] text-slate-600"
                >
                  <IconC className="h-3 w-3 text-slate-400" />
                  <span className="font-mono">{id.value}</span>
                  <span className="text-slate-400">×{id.record_count ?? id.count ?? 1}</span>
                </span>
              );
            })}
          </div>
        </section>
      )}
    </>
  );
}

function HopNode({ hop, open, onToggle }) {
  return (
    <div className="relative pl-10">
      <div
        className={`absolute left-0 top-2 grid h-8 w-8 place-items-center rounded-full text-xs font-bold text-white shadow-sm ${
          hop.hop === 0 ? "bg-accent-500" : "bg-gradient-to-br from-grape to-violet-500"
        }`}
      >
        {hop.hop}
      </div>
      <button
        onClick={onToggle}
        className="w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-left transition hover:border-accent-300 hover:shadow-sm"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <span className="text-sm font-semibold text-ink-900">
              {hop.hop === 0 ? "Seed identifiers" : `Hop ${hop.hop}`}
            </span>
            <span className="ml-2 text-xs text-slate-400">
              +{hop.new_records} records · {hop.datasets_touched?.length ?? 0} datasets · {hop.duration_ms} ms
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            {(hop.sources_touched || []).map((s) => (
              <span key={s} className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
                {s}
              </span>
            ))}
            <ChevronDown className={`h-4 w-4 text-slate-400 transition ${open ? "rotate-180" : ""}`} />
          </div>
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {hop.seed_identifiers?.slice(0, 6).map((sid) => (
            <IdentifierChip key={`${sid.type}:${sid.value}`} id={sid} />
          ))}
          {(!hop.seed_identifiers || hop.seed_identifiers.length === 0) && (
            <span className="text-[11px] text-slate-400">discovered via previous hop</span>
          )}
        </div>
      </button>
      {open && (
        <div className="mt-2 rounded-xl border border-slate-100 bg-ink-50 p-3">
          <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">New identifiers found</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {hop.new_identifiers?.map((id) => (
              <IdentifierChip key={`${id.type}:${id.value}`} id={id} />
            ))}
            {(!hop.new_identifiers || hop.new_identifiers.length === 0) && (
              <span className="text-xs text-slate-400">none beyond the seed</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function IdentifierChip({ id }) {
  const IconC = TYPE_ICONS[id.type] || GitMerge;
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-white px-1.5 py-0.5 text-[10px] text-slate-600 ring-1 ring-slate-200">
      <IconC className="h-2.5 w-2.5 text-grape" />
      <span className="font-mono">{id.type}:</span>
      <span className="font-mono font-semibold">{id.value}</span>
    </span>
  );
}

function BreakdownCard({ result }) {
  const sourceRows = Object.entries(result.source_breakdown || {});
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-card">
      <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-ink-900">
        <Database className="h-4 w-4 text-slate-400" /> Sources touched
      </h2>
      {sourceRows.length === 0 ? (
        <p className="text-xs text-slate-400">No sources.</p>
      ) : (
        <div className="space-y-2">
          {sourceRows.map(([name, count]) => {
            const total = result.record_count || 1;
            const pct = (count / total) * 100;
            return (
              <div key={name}>
                <div className="flex items-center justify-between text-xs">
                  <span className="font-mono truncate text-slate-600">{name}</span>
                  <span className="tabular-nums text-slate-400">{count}</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-accent-400 to-grape"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
      {result.hop_breakdown && Object.keys(result.hop_breakdown).length > 0 && (
        <div className="mt-4 border-t border-slate-100 pt-3">
          <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Records per hop</div>
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(result.hop_breakdown).map(([hop, count]) => (
              <span key={hop} className="rounded-md bg-slate-100 px-2 py-1 text-[11px] tabular-nums text-slate-600">
                hop {hop} · {count}
              </span>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}