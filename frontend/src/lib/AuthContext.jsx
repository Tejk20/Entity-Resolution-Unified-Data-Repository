import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { apiV1, getToken, setToken } from "../lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("loading"); // loading | authed | unauthed

  useEffect(() => {
    if (!getToken()) {
      setStatus("unauthed");
      return;
    }
    apiV1
      .me()
      .then((u) => {
        setUser(u);
        setStatus("authed");
      })
      .catch(() => {
        setToken(null);
        setStatus("unauthed");
      });
  }, []);

  const authenticate = useCallback(async (token, user) => {
    setToken(token);
    setUser(user);
    setStatus("authed");
    return user;
  }, []);

  const login = useCallback(
    async (email, password) => {
      const res = await apiV1.login({ email, password });
      return authenticate(res.access_token, res.user);
    },
    [authenticate]
  );

  const register = useCallback(
    async (payload) => {
      const res = await apiV1.register(payload);
      return authenticate(res.access_token, res.user);
    },
    [authenticate]
  );

  const logout = useCallback(async () => {
    try {
      await apiV1.logout();
    } catch {
      /* token is discarded below regardless */
    }
    setToken(null);
    setUser(null);
    setStatus("unauthed");
  }, []);

  const value = useMemo(
    () => ({ user, status, login, register, authenticate, logout }),
    [user, status, login, register, authenticate, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}