import { getBackendApiBaseUrl } from "./gateway-url";

export class BackendInternalError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "BackendInternalError";
    this.status = status;
    this.payload = payload;
  }
}

export function readInternalApiToken(): string {
  return (
    process.env.INTERNAL_API_TOKEN ??
    process.env.INTERNAL_CONFIG_TOKEN ??
    ""
  ).trim();
}

export async function callBackendInternal(
  path: string,
  init: RequestInit = {},
): Promise<{ payload: unknown; status: number }> {
  const token = readInternalApiToken();
  if (!token) {
    throw new BackendInternalError(
      "INTERNAL_API_TOKEN not configured",
      503,
      { error: { code: "INTERNAL_API_TOKEN_MISSING", message: "INTERNAL_API_TOKEN not configured" } },
    );
  }

  const base = getBackendApiBaseUrl();
  const url = `${base}${path.startsWith("/") ? path : `/${path}`}`;
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(url, {
    ...init,
    headers,
    cache: "no-store",
    signal: init.signal ?? AbortSignal.timeout(10_000),
  });
  const payload = await readResponsePayload(res);
  return { payload, status: res.status };
}

export function extractErrorMessage(payload: unknown, fallback = "request failed"): string {
  if (payload && typeof payload === "object" && "error" in payload) {
    const error = (payload as { error?: unknown }).error;
    if (typeof error === "string") return error;
    if (error && typeof error === "object" && "message" in error) {
      const message = (error as { message?: unknown }).message;
      if (typeof message === "string" && message) return message;
    }
  }
  return fallback;
}

async function readResponsePayload(res: Response): Promise<unknown> {
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return res.json().catch(() => null);
  }
  const text = await res.text().catch(() => "");
  return text ? { error: text } : null;
}
