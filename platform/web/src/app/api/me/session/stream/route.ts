import { NextRequest } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { getGatewayHttpBaseUrl } from "@/lib/gateway-url";
import { getPlatformTokenForUser } from "@/lib/internal-auth";
import { isPlatformAccessError } from "@/lib/platform-access";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const corsHeaders: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization",
};

export async function OPTIONS() {
  return new Response(null, { status: 204, headers: corsHeaders });
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url);

  // 阶段四 A4a：双轨鉴权
  // 兼容 SSE 场景：浏览器 EventSource 不支持自定义 header，允许 ?token=xxx
  // query 参数同样走双轨鉴权（构造一个合成的 Authorization header 给 authenticateRequest）
  let authReq: Request = req;
  const queryToken = (url.searchParams.get("token") ?? "").trim();
  const hasAuthHeader =
    !!(req.headers.get("authorization") ?? req.headers.get("Authorization"));
  if (!hasAuthHeader && queryToken) {
    const headers = new Headers(req.headers);
    headers.set("Authorization", `Bearer ${queryToken}`);
    authReq = new Request(req.url, { method: req.method, headers });
  }

  const authedUser = await authenticateRequest(authReq);
  if (!authedUser) {
    return new Response(JSON.stringify({ error: "unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json", ...corsHeaders },
    });
  }

  // WS HTTP API 只认 platform_token；反查后透传
  let platformToken: string | null;
  try {
    platformToken = await getPlatformTokenForUser(authedUser.user_id);
  } catch (error) {
    if (isPlatformAccessError(error)) {
      return new Response(JSON.stringify({ error: error.code, reason: error.code }), {
        status: error.status,
        headers: { "Content-Type": "application/json", ...corsHeaders },
      });
    }
    throw error;
  }
  if (!platformToken) {
    return new Response(
      JSON.stringify({ error: "platform_token_unavailable" }),
      {
        status: 500,
        headers: { "Content-Type": "application/json", ...corsHeaders },
      },
    );
  }

  const base = getGatewayHttpBaseUrl();
  const kind = url.searchParams.get("kind") === "logs" ? "logs" : "session";
  let target: string;
  if (kind === "logs") {
    target = `${base}/api/v1/agent/logs/stream`;
  } else {
    const currentRes = await fetch(`${base}/api/v1/sessions/current`, {
      headers: { Authorization: `Bearer ${platformToken}` },
      cache: "no-store",
    });
    if (!currentRes.ok) {
      const body = await currentRes.text();
      return new Response(body, {
        status: currentRes.status,
        headers: { "Content-Type": "application/json", ...corsHeaders },
      });
    }
    const current = (await currentRes.json()) as { agent_instance_id?: string; online?: boolean };
    if (!current.agent_instance_id) {
      return new Response(JSON.stringify({ error: "offline" }), {
        status: 409,
        headers: { "Content-Type": "application/json", ...corsHeaders },
      });
    }
    target = `${base}/api/v1/sessions/${current.agent_instance_id}/stream`;
  }

  const upstream = await fetch(target, {
    headers: { Authorization: `Bearer ${platformToken}`, Accept: "text/event-stream" },
    signal: req.signal,
  });

  if (!upstream.ok || !upstream.body) {
    const body = await upstream.text().catch(() => "");
    return new Response(body || JSON.stringify({ error: "upstream failed" }), {
      status: upstream.status || 502,
      headers: { "Content-Type": "application/json", ...corsHeaders },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
      // 防 ?token=xxx query 经 Referer 泄漏到第三方站点（其它日志层 redact 见 lib/log-redact.ts）
      "Referrer-Policy": "no-referrer",
      ...corsHeaders,
    },
  });
}
