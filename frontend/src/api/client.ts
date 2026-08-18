// SkyTrap runs on localhost:8000 in dev (Vite is on :5173) and is served from
// the same origin once frontend/dist is built and mounted by FastAPI — in that
// case window.location.origin already *is* the API origin.
const API_BASE = import.meta.env.DEV ? "http://localhost:8000" : "";

export class UnauthenticatedError extends Error {}

async function rawRequest(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init.headers },
  });
}

let refreshInFlight: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  // Multiple concurrent 401s (e.g. GET /auth/me + WS handshake failing at once)
  // must not fire multiple /auth/refresh calls — the refresh token rotates on
  // use, so a second concurrent call would invalidate the first's new token.
  if (refreshInFlight === null) {
    refreshInFlight = rawRequest("/auth/refresh", { method: "POST" })
      .then((response) => response.ok)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

/** Issues an authenticated API request, transparently retrying once via
 * /auth/refresh on a 401 (the access token is short-lived by design — 15 min).
 * Throws UnauthenticatedError if refresh also fails, so callers can fall back
 * to the login screen. */
export async function apiRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const first = await rawRequest(path, init);
  if (first.status !== 401) return first;

  const refreshed = await refreshSession();
  if (!refreshed) throw new UnauthenticatedError();

  return rawRequest(path, init);
}

export async function apiJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiRequest(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${response.status}`);
  }
  return response.json();
}

export { API_BASE };
