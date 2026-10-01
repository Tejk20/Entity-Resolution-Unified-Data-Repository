import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "../lib/AuthContext";
import { apiUrl, postAuthJson, setToken } from "../lib/api";
import { AuthShell, ErrorMessage, Field, SubmitButton } from "./Login";

export default function Register() {
  const navigate = useNavigate();
  const { authenticate } = useAuth();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e) {
    e.preventDefault(); // never let the browser hit the API with a raw HTML POST
    setError("");
    if (password.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      const res = await postAuthJson(apiUrl("/auth/register"), {
        email,
        password,
        full_name: fullName.trim() || undefined,
      });
      setToken(res.access_token);
      await authenticate(res.access_token, res.user);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err.message || "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell
      eyebrow="Entity Resolution"
      title="Create your account"
      subtitle="A single account unlocks import, search and entity workflows."
      footer={
        <>
          Already registered?{" "}
          <Link to="/login" className="font-medium text-accent-600 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <ErrorMessage id="error-message" message={error} />
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <Field
          label="Full name (optional)"
          name="full_name"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          autoComplete="name"
          placeholder="Ada Lovelace"
        />
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
          autoComplete="new-password"
          placeholder="min. 6 characters"
        />
        <Field
          label="Confirm password"
          name="password_confirm"
          type="password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password"
          placeholder="repeat the password"
        />
        <SubmitButton busy={busy}>Create account</SubmitButton>
      </form>
    </AuthShell>
  );
}