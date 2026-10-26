// @ts-nocheck
// DEPRECATED: 此 Edge Function 已废弃。
// 业务逻辑全部迁移到 Next.js 平台（POST /api/register）。
// Supabase 仅保留 Auth + DB 职责，不再承载注册业务。

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-admin-secret, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
};

Deno.serve((req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders });
  }

  return new Response(
    JSON.stringify({
      error: "endpoint_deprecated",
      message:
        "This endpoint has been removed. Please use POST /api/register on the Next.js app.",
      moved_to: "/api/register",
    }),
    {
      status: 410,
      headers: { ...corsHeaders, "Content-Type": "application/json" },
    },
  );
});
