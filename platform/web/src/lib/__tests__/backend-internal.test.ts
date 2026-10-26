import { test } from "node:test";
import assert from "node:assert/strict";

import {
  callBackendInternal,
  extractErrorMessage,
  readInternalApiToken,
} from "../backend-internal";

test("readInternalApiToken prefers INTERNAL_API_TOKEN", () => {
  const oldInternal = process.env.INTERNAL_API_TOKEN;
  const oldConfig = process.env.INTERNAL_CONFIG_TOKEN;
  process.env.INTERNAL_API_TOKEN = "internal-token";
  process.env.INTERNAL_CONFIG_TOKEN = "legacy-token";
  try {
    assert.equal(readInternalApiToken(), "internal-token");
  } finally {
    process.env.INTERNAL_API_TOKEN = oldInternal;
    process.env.INTERNAL_CONFIG_TOKEN = oldConfig;
  }
});

test("readInternalApiToken falls back to legacy INTERNAL_CONFIG_TOKEN", () => {
  const oldInternal = process.env.INTERNAL_API_TOKEN;
  const oldConfig = process.env.INTERNAL_CONFIG_TOKEN;
  delete process.env.INTERNAL_API_TOKEN;
  process.env.INTERNAL_CONFIG_TOKEN = "legacy-token";
  try {
    assert.equal(readInternalApiToken(), "legacy-token");
  } finally {
    process.env.INTERNAL_API_TOKEN = oldInternal;
    process.env.INTERNAL_CONFIG_TOKEN = oldConfig;
  }
});

test("extractErrorMessage reads nested backend error shape", () => {
  assert.equal(
    extractErrorMessage({ error: { code: "X", message: "backend failed" } }, "fallback"),
    "backend failed",
  );
  assert.equal(extractErrorMessage({ error: "plain failed" }, "fallback"), "plain failed");
  assert.equal(extractErrorMessage({}, "fallback"), "fallback");
});

test("callBackendInternal prefers PLATFORM_BACKEND_API_URL without changing PLATFORM_WS_HTTP_URL", async () => {
  const oldFetch = globalThis.fetch;
  const oldInternal = process.env.INTERNAL_API_TOKEN;
  const oldBackend = process.env.PLATFORM_BACKEND_API_URL;
  const oldWsHttp = process.env.PLATFORM_WS_HTTP_URL;
  let seenUrl = "";

  process.env.INTERNAL_API_TOKEN = "internal-token";
  process.env.PLATFORM_BACKEND_API_URL = "http://backend-api.test/api";
  process.env.PLATFORM_WS_HTTP_URL = "http://legacy-backend.test";
  globalThis.fetch = (async (url: RequestInfo | URL) => {
    seenUrl = String(url);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;

  try {
    await callBackendInternal("/internal/persona/parse", { method: "POST", body: "{}" });
    assert.equal(seenUrl, "http://backend-api.test/api/internal/persona/parse");
  } finally {
    globalThis.fetch = oldFetch;
    process.env.INTERNAL_API_TOKEN = oldInternal;
    process.env.PLATFORM_BACKEND_API_URL = oldBackend;
    process.env.PLATFORM_WS_HTTP_URL = oldWsHttp;
  }
});
