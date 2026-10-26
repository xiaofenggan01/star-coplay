import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";
import {
  readStarcoplayConfigEnvironment,
  makeSupabaseFetcher,
  readSystemConfig,
  ConfigError,
} from "@/lib/system-config";

// GET /api/internal/configs/[domain]/[configKey]?env=dev|test|prod
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ domain: string; configKey: string }> },
) {
  const token = (
    process.env.INTERNAL_API_TOKEN ??
    process.env.INTERNAL_CONFIG_TOKEN ??
    ""
  ).trim();
  if (!token) {
    return NextResponse.json(
      { error: { code: "CONFIG_SERVICE_ERROR", message: "INTERNAL_API_TOKEN not configured" } },
      { status: 503 },
    );
  }

  const provided =
    (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "").trim() ||
    (req.headers.get("x-internal-api-token") ?? "").trim() ||
    (req.headers.get("x-internal-config-token") ?? "").trim();

  if (provided !== token) {
    return NextResponse.json(
      { error: { code: "CONFIG_UNAUTHORIZED", message: "unauthorized" } },
      { status: 401 },
    );
  }

  try {
    const { domain, configKey } = await params;
    const envParam = new URL(req.url).searchParams.get("env");
    const environment = readStarcoplayConfigEnvironment(envParam);

    const supabase = createServiceClient();
    const fetcher = makeSupabaseFetcher(supabase);
    const result = await readSystemConfig({ fetcher, domain, configKey, environment });

    return NextResponse.json({
      domain: result.domain,
      config_key: result.config_key,
      environment: result.environment,
      value_type: result.value_type,
      value_type_code: result.value_type_code,
      config_value: result.config_value,
      parsed_value: result.parsed_value,
      updated_at: result.updated_at,
    });
  } catch (err) {
    if (err instanceof ConfigError) {
      const status =
        err.code === "CONFIG_NOT_FOUND" ? 404
        : err.code === "CONFIG_DISABLED" ? 409
        : err.code === "CONFIG_PARSE_ERROR" ? 500
        : 400;
      return NextResponse.json(
        { error: { code: err.code, message: err.message } },
        { status },
      );
    }
    return NextResponse.json(
      { error: { code: "CONFIG_SERVICE_ERROR", message: String(err) } },
      { status: 500 },
    );
  }
}
