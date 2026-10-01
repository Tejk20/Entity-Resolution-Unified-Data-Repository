import { GitMerge, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "../lib/AuthContext";
import { apiUrl, postAuthJson, setToken } from "../lib/api";

export function AuthShell({ eyebrow, title, subtitle, children, footer }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-50 px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="grid h-12 w-12 place-items-center rounded-2xl bg-gradient-to-br from-accent-500 to-grape text-white shadow-lift">
            <GitMerge className="h-6 w-6" />
          </div>
          <div className="mt-3 text-[11px] font-semibold uppercase tracking-wide text-grape">{eyebrow}</div>
          <h1 className="mt-1 text-xl font-bold text-ink-900">{title}</h1>
          <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-card">
          {children}
        </div>

        {footer && <div className="mt-4 text-center text-sm text-slate-500">{footer}</div>}
      </div>
    </div>
  );
}

export function ErrorMessage({ id = "error-message", message }) {
  if (!message) return null;
  return (
    <div
      id={id}
      role="alert"
      className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
    >
      {message}
    </div>
  );
}

export function Field({ label, type = "text", name, value, onChange, autoComplete, placeholder }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-slate-600">{label}</span>
      <input
        name={name}
        type={type}
        value={value}
        onChange={onChange}
        autoComplete={autoComplete}
        placeholder={placeholder}
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-ink-900 placeholder:text-slate-400 focus:border-accent-400 focus:outline-none focus:ring-2 focus:ring-accent-300"
      />
    </label>
  );
}

export function SubmitButton({ children, busy }) {
  return (
    <button
      type="submit"
      disabled={busy}
      className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent-600 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-accent-500 disabled:cursor-not-allowed disabled:opacity-60"
    >
      {busy && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

export default function Login() {
  const navigate = useNavigate();
  const { authenticate } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e) {
    e.preventDefault(); // keep the browser from navigating to a raw JSON page
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      const res = await postAuthJson(apiUrl("/auth/login"), { email, password });
      setToken(res.access_token);
      await authenticate(res.access_token, res.user);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.message || "Invalid credentials");
    } finally {
      setBusy(false);
    }
  }

  // if the session is already valid, the route guard redirects away automatically.

  return (
    <AuthShell
      eyebrow="Entity Resolution"
      title="Sign in to your account"
      subtitle="Continue to the unified data repository."
      footer={
        <>
          Don't have an account?{" "}
          <Link to="/register" className="font-medium text-accent-600 hover:underline">
            Create one
          </Link>
        </>
      }
    >
      <ErrorMessage id="error-message" message={error} />
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <Field
          label="Email"
          name="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          placeholder="you@example.com"
        />
        <Field
          label="Password"
          name="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          placeholder="••••••••"
        />
        <SubmitButton busy={busy}>Sign in</SubmitButton>
      </form>
    </AuthShell>
  );
}