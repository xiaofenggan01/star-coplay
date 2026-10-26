const LOCAL_GATEWAY_HTTP_URL = "http://127.0.0.1:8766";

const WS_GATEWAY_URL_ENV_KEYS = [
  "PLATFORM_WS_URL",
  "WS_GATEWAY_URL",
  "WS_URL",
  "NEXT_PUBLIC_WS_GATEWAY_URL",
  "NEXT_PUBLIC_WS_URL",
];

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function normalizeHttpUrl(value: string | undefined): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    url.pathname = trimTrailingSlash(url.pathname);
    url.search = "";
    url.hash = "";
    return trimTrailingSlash(url.toString());
  } catch {
    return null;
  }
}

function deriveHttpUrlFromWsUrl(value: string | undefined): string | null {
  const raw = value?.trim();
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "ws:" && url.protocol !== "wss:") return null;
    url.protocol = url.protocol === "wss:" ? "https:" : "http:";
    if (url.port === "8765") url.port = "8766";
    url.pathname = "";
    url.search = "";
    url.hash = "";
    return trimTrailingSlash(url.toString());
  } catch {
    return null;
  }
}

export function getGatewayHttpBaseUrl(): string {
  const configured = normalizeHttpUrl(process.env.PLATFORM_WS_HTTP_URL);
  if (configured) return configured;

  for (const key of WS_GATEWAY_URL_ENV_KEYS) {
    const derived = deriveHttpUrlFromWsUrl(process.env[key]);
    if (derived) return derived;
  }

  return LOCAL_GATEWAY_HTTP_URL;
}

export function getBackendApiBaseUrl(): string {
  return normalizeHttpUrl(process.env.PLATFORM_BACKEND_API_URL) ?? getGatewayHttpBaseUrl();
}
