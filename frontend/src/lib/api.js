import axios from "axios";

// Empty/unset means same-origin: the dev proxy and the production nginx
// container both forward /api/* to the backend. A full origin is used verbatim.
const raw = (import.meta.env.VITE_API_BASE_URL || "").trim();

const baseURL = `${raw}/api/v1`;

//: absolute URL for plain fetch() based calls (used by the auth pages)
export function apiUrl(path) {
  return `${raw}/api/v1${path}`;
}

//: fetch() POST helper used by the auth forms; normalizes FastAPI error shapes
export async function postAuthJson(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      Array.isArray(data?.detail)
        ? data.detail.map((d) => d.msg).join("; ")
        : data?.detail || `request failed (${res.status})`
    );
  }
  return data;
}

export const api = axios.create({
  baseURL,
  // Render's free tier sleeps after 15 min idle and needs ~50s to wake, so a
  // cold first request can sit idle well past a 60s budget before any work
  // happens server-side.
  timeout: 120000,
  headers: { Accept: "application/json" },
});

const TOKEN_KEY = "er_access_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

// attach the bearer token to every request when present
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    const status = err?.response?.status;
    const detail = err?.response?.data?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join("; ")
      : detail || err.message || "request failed";

    // expired / missing token: clear it and bounce to the login screen,
    // but never redirect when the request was itself an auth attempt.
    const path = err?.config?.url || "";
    if (status === 401 && !/\/auth\/(login|register)/.test(path)) {
      setToken(null);
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(new Error(message));
  }
);

export const apiV1 = {
  // ---------------------------------------------------------------- auth -----
  register: (payload) => api.post("/auth/register", payload).then((r) => r.data),
  login: (payload) => api.post("/auth/login", payload).then((r) => r.data),
  me: () => api.get("/auth/me").then((r) => r.data),
  logout: () => api.post("/auth/logout").then((r) => r.data),

  stats: () => api.get("/stats").then((r) => r.data),
  statsBreakdown: () => api.get("/stats/breakdown").then((r) => r.data),
  health: () => api.get("/health").then((r) => r.data),

  sources: () => api.get("/sources").then((r) => r.data),
  source: (id) => api.get(`/sources/${id}`).then((r) => r.data),
  sourceSummary: (id) => api.get(`/sources/${id}/summary`).then((r) => r.data),
  datasets: () => api.get("/sources/datasets").then((r) => r.data),
  deleteSource: (id) => api.delete(`/sources/${id}`).then((r) => r.data),
  sourcesBreakdown: () => api.get("/sources/breakdown").then((r) => r.data),

  upload: (file, opts = {}) => {
    const form = new FormData();
    form.append("file", file);
    if (opts.name) form.append("name", opts.name);
    if (opts.description) form.append("description", opts.description);
    if (opts.source_id) form.append("source_id", String(opts.source_id));
    if (opts.auto_confirm) form.append("auto_confirm", "true");
    if (opts.mapping_json) form.append("mapping_json", opts.mapping_json);
    return api.post("/sources/upload", form, {
      headers: { "Content-Type": "multipart/form-data" },
    }).then((r) => r.data);
  },

  jobs: (params) =>
    api.get("/sources/jobs/all", { params }).then((r) => r.data),
  job: (id) => api.get(`/sources/jobs/${id}`).then((r) => r.data),
  jobByUid: (uid) => api.get(`/sources/jobs/by-uid/${uid}`).then((r) => r.data),
  retryJob: (id) => api.post(`/sources/jobs/${id}/retry`).then((r) => r.data),

  mappingPreview: (jobId) => api.get(`/mapping/jobs/${jobId}`).then((r) => r.data),
  mappingRescan: (jobId) => api.post(`/mapping/jobs/${jobId}/rescan`).then((r) => r.data),
  confirmMapping: (jobId, payload) =>
    api.post(`/mapping/jobs/${jobId}/confirm`, payload).then((r) => r.data),
  canonicalFields: () => api.get("/mapping/canonical-fields").then((r) => r.data),

  search: (payload) => api.post("/search", payload).then((r) => r.data),
  suggest: (q) => api.get("/search/suggest", { params: { q } }).then((r) => r.data),
  recentRuns: (limit = 25) => api.get("/runs", { params: { limit } }).then((r) => r.data),
  run: (uid) => api.get(`/runs/${uid}`).then((r) => r.data),

  entities: (params) => api.get("/entities", { params }).then((r) => r.data),
  entity: (id) => api.get(`/entities/${id}`).then((r) => r.data),
  entityTimeline: (id) => api.get(`/entities/${id}/timeline`).then((r) => r.data),
  entityRecords: (id) => api.get(`/entities/${id}/records`).then((r) => r.data),

  seed: (fmt = "csv") =>
    api.post("/seed", null, { params: { fmt } }).then((r) => r.data),
  seedSummary: () => api.get("/seed/summary").then((r) => r.data),
  rebuild: () => api.post("/maintenance/rebuild-clusters").then((r) => r.data),
};

//: poll helper used by progress bars
export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}