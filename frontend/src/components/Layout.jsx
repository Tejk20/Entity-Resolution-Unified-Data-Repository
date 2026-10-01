import { Activity, Bell, Database, GitMerge, LogOut, Search, Upload } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../lib/AuthContext";

const links = [
  { to: "/", label: "Dashboard", icon: Database, end: true },
  { to: "/import", label: "Import Data", icon: Upload },
  { to: "/search", label: "Repository Search", icon: Search },
  { to: "/entities", label: "Master Entities", icon: GitMerge },
  { to: "/jobs", label: "Processing Jobs", icon: Activity },
];

export default function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const inSearch = location.pathname.startsWith("/search");

  const initial = (user?.full_name || user?.email || "U").charAt(0).toUpperCase();

  async function onLogout() {
    await logout();
    navigate("/login", { replace: true });
  }

  return (
    <div className="min-h-screen bg-ink-50 text-slate-800">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-gradient-to-br from-accent-500 to-grape text-white">
              <GitMerge className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-semibold leading-tight text-ink-900">
                Entity Resolution
              </div>
              <div className="text-[11px] leading-tight text-slate-500">
                Unified Data Repository · Progressive Enrichment
              </div>
            </div>
          </div>
          <div className="hidden items-center gap-1 sm:flex">
            {links.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.end}
                className={({ isActive }) =>
                  `flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition ${
                    isActive
                      ? "bg-accent-500 text-white shadow-sm"
                      : "text-slate-600 hover:bg-slate-100"
                  }`
                }
              >
                <l.icon className="h-3.5 w-3.5" />
                {l.label}
              </NavLink>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button className="grid h-9 w-9 place-items-center rounded-full border border-slate-200 text-slate-500 hover:bg-slate-50">
              <Bell className="h-4 w-4" />
            </button>
            <div className="flex items-center gap-2 rounded-full border border-slate-200 py-1 pl-1 pr-2">
              <span className="grid h-7 w-7 place-items-center rounded-full bg-gradient-to-br from-accent-500 to-grape text-[11px] font-bold text-white">
                {initial}
              </span>
              <span className="hidden max-w-[160px] truncate text-xs font-medium text-slate-700 lg:block">
                {user?.full_name || user?.email}
              </span>
              <button
                onClick={onLogout}
                title="Log out"
                className="grid h-6 w-6 place-items-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-rose-500"
              >
                <LogOut className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        </div>
      </header>

      {inSearch && <div className="h-1.5 animate-pulse bg-accent-500/20" />}

      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>

      <footer className="mx-auto max-w-7xl px-4 pb-8">
        <div className="flex items-center justify-between border-t border-slate-200 pt-4 text-[11px] text-slate-400">
          <span>Multi-Database Entity Resolution · PostgreSQL · Redis · Celery · FastAPI</span>
          <span className="hidden sm:inline">John Doe bridge: 4 hops → 4 datasets</span>
        </div>
      </footer>
    </div>
  );
}

