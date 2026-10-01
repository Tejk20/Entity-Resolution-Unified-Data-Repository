import { Loader2 } from "lucide-react";

export function Spinner({ className = "h-4 w-4" }) {
  return <Loader2 className={`${className} animate-spin text-accent-500`} />;
}

export function StatCard({ label, value, sub, icon: Icon, tone = "sky" }) {
  const tones = {
    sky: "bg-sky-50 text-sky-600",
    grape: "bg-violet-50 text-violet-600",
    mint: "bg-emerald-50 text-emerald-600",
    coral: "bg-rose-50 text-rose-600",
    amber: "bg-amber-50 text-amber-600",
    slate: "bg-slate-100 text-slate-600",
  };
  return (
    <div className="flex items-start gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-card">
      <div className={`grid h-10 w-10 shrink-0 place-items-center rounded-lg ${tones[tone]}`}>
        <Icon className="h-5 w-5" />
      </div>
      <div className="min-w-0">
        <div className="truncate text-[11px] font-medium uppercase tracking-wide text-slate-500">
          {label}
        </div>
        <div className="text-2xl font-bold tabular-nums text-ink-900">{value}</div>
        {sub && <div className="truncate text-xs text-slate-500">{sub}</div>}
      </div>
    </div>
  );
}

const barTones = {
  accent: "bg-accent-500",
  emerald: "bg-emerald-500",
  sky: "bg-sky-500",
  grape: "bg-grape",
  mint: "bg-mint",
};

export function ProgressBar({ value, tone = "accent", label }) {
  const pct = Math.max(0, Math.min(100, Math.round((value ?? 0) * 100)));
  return (
    <div>
      {label && (
        <div className="mb-1 flex items-center justify-between text-xs">
          <span className="font-medium text-slate-600">{label}</span>
          <span className="tabular-nums text-slate-500">{pct}%</span>
        </div>
      )}
      <div className="h-2 overflow-hidden rounded-full bg-slate-200">
        <div
          className={`h-full rounded-full ${barTones[tone] || barTones.accent} transition-all duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function Badge({ children, tone = "slate" }) {
  const tones = {
    slate: "bg-slate-100 text-slate-700",
    sky: "bg-sky-100 text-sky-700",
    emerald: "bg-emerald-100 text-emerald-700",
    amber: "bg-amber-100 text-amber-800",
    rose: "bg-rose-100 text-rose-700",
    violet: "bg-violet-100 text-violet-700",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

export function statusTone(status) {
  const s = (status || "").toLowerCase();
  if (s.includes("completed")) return "emerald";
  if (s.includes("fail")) return "rose";
  if (s.includes("pending") || s.includes("uploaded") || s.includes("mapping")) return "amber";
  return "sky";
}

export function EmptyState({ icon: Icon, title, hint }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-300 bg-white/60 px-6 py-12 text-center">
      <div className="mb-3 grid h-12 w-12 place-items-center rounded-full bg-slate-100 text-slate-400">
        <Icon className="h-6 w-6" />
      </div>
      <div className="text-sm font-semibold text-slate-700">{title}</div>
      {hint && <div className="mt-1 max-w-sm text-xs text-slate-500">{hint}</div>}
    </div>
  );
}