/**
 * Typed client for the Cortex API, called through the same-origin proxy (/api/backend/*).
 * The session cookie travels automatically; no token is handled in browser code.
 */
import type {
  AgentSummary,
  ApiErrorBody,
  Dashboard,
  Execution,
  Page,
  Project,
  ProjectCreate,
  ProjectStatus,
  ProjectSummary,
  Report,
  RunStatus,
  SettingsUpdate,
  Source,
  Task,
  User,
  UserSettings,
} from "@/types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details?: unknown,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "network_error", "Can't reach Cortex. Check your connection and try again.");
  }
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth/")) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    // Full navigation on purpose: an expired session should drop all client state.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign(`/login?next=${next}`);
  }
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    const err = (body as ApiErrorBody | null)?.error;
    throw new ApiError(res.status, err?.code ?? "error", err?.message ?? `Request failed (${res.status})`, err?.details);
  }
  return body as T;
}

const api = (path: string) => `/api/backend${path}`;
const json = (body: unknown) => JSON.stringify(body);

export const auth = {
  login: (email: string, password: string) =>
    request<{ user: User }>("/api/auth/login", { method: "POST", body: json({ email, password }) }),
  register: (email: string, password: string) =>
    request<{ user: User }>("/api/auth/register", { method: "POST", body: json({ email, password }) }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  me: () => request<User>(api("/auth/me")),
};

export const research = {
  create: (body: ProjectCreate) => request<Project>(api("/research"), { method: "POST", body: json(body) }),
  list: (params: { limit?: number; offset?: number; status?: ProjectStatus } = {}) => {
    const q = new URLSearchParams();
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    if (params.status) q.set("status", params.status);
    return request<Page<ProjectSummary>>(api(`/research?${q}`));
  },
  get: (id: string) => request<Project>(api(`/research/${id}`)),
  start: (id: string) => request<RunStatus>(api(`/research/${id}/start`), { method: "POST" }),
  cancel: (id: string) => request<RunStatus>(api(`/research/${id}/cancel`), { method: "POST" }),
  approve: (id: string, approved: boolean, reason?: string) =>
    request<RunStatus>(api(`/research/${id}/approve`), { method: "POST", body: json({ approved, reason }) }),
  status: (id: string) => request<RunStatus>(api(`/research/${id}/status`)),
  tasks: (id: string) => request<Task[]>(api(`/research/${id}/tasks`)),
  agents: (id: string) => request<AgentSummary[]>(api(`/research/${id}/agents`)),
  executions: (id: string, limit = 50, offset = 0) =>
    request<Page<Execution>>(api(`/research/${id}/executions?limit=${limit}&offset=${offset}`)),
  sources: (id: string, limit = 100, offset = 0) =>
    request<Page<Source>>(api(`/research/${id}/sources?limit=${limit}&offset=${offset}`)),
  report: (id: string, version?: number) =>
    request<Report>(api(`/research/${id}/report${version ? `?version=${version}` : ""}`)),
  eventsUrl: (id: string) => api(`/research/${id}/events`),
  pdfUrl: (id: string, version?: number) => api(`/research/${id}/report/pdf${version ? `?version=${version}` : ""}`),
  markdownUrl: (id: string, version?: number) =>
    api(`/research/${id}/report/download${version ? `?version=${version}` : ""}`),
};

export const dashboard = {
  get: () => request<Dashboard>(api("/dashboard")),
};

export const settings = {
  get: () => request<UserSettings>(api("/settings")),
  update: (body: SettingsUpdate) => request<UserSettings>(api("/settings"), { method: "PUT", body: json(body) }),
};
