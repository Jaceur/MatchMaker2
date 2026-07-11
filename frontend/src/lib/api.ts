// Tiny typed fetch wrapper around the FastAPI backend.
// - Base URL from NEXT_PUBLIC_API_URL (defaults to localhost:8000).
// - Attaches the stored Bearer token to every request.
// - Throws ApiError on non-2xx so callers can try/catch; 204 returns null.

// Normalise the configured API URL: trim, drop any trailing slash, and — the
// important bit — prepend https:// if the scheme was omitted. Without a scheme
// the browser treats the value as a *relative path* and posts to the Vercel
// origin instead of the API, yielding a confusing 404.
function normalizeBaseUrl(raw?: string): string {
  const value = raw?.trim();
  if (!value) return "http://localhost:8000";
  const noTrailing = value.replace(/\/$/, "");
  return /^https?:\/\//i.test(noTrailing) ? noTrailing : `https://${noTrailing}`;
}

const BASE_URL = normalizeBaseUrl(process.env.NEXT_PUBLIC_API_URL);

const TOKEN_KEY = "mm_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  auth?: boolean; // default true
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Is the API running?");
  }

  if (res.status === 401 && typeof window !== "undefined") {
    // Token missing/expired — drop it so the guard redirects to login.
    setToken(null);
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (data?.detail) {
        detail = Array.isArray(data.detail)
          ? data.detail.map((d: { msg?: string }) => d.msg).join(", ")
          : data.detail;
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return null as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : null) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: "POST", body }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  del: <T>(path: string) => request<T>(path, { method: "DELETE" }),
  // login is the one unauthenticated call
  login: (username: string, password: string) =>
    request<{ access_token: string; user: { username: string; role: string } }>(
      "/auth/login",
      { method: "POST", body: { username, password }, auth: false },
    ),
};
