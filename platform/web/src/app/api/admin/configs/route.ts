import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";
import {
  readStarcoplayConfigEnvironment,
  makeSupabaseFetcher,
  validateSystemConfigPayload,
  ConfigError,
  type SystemConfigResponse,
} from "@/lib/system-config";

function checkAdmin(req: NextRequest): boolean {
  const secret = (process.env.ADMIN_SECRET ?? "").trim();
  if (!secret) return false;
  const token =
    (req.headers.get("x-admin-secret") ?? "").trim() ||
    (req.headers.get("authorization") ?? "").replace(/^Bearer\s+/i, "").trim();
  return token === secret;
}

function configError(err: unknown) {
  if (err instanceof ConfigError) {
    const status =
      err.code === "CONFIG_NOT_FOUND"
        ? 404
        : err.code === "CONFIG_DISABLED" || err.code === "CONFIG_ALREADY_EXISTS"
          ? 409
          : err.code === "CONFIG_UNAUTHORIZED"
            ? 401
            : err.code === "CONFIG_SERVICE_ERROR" ||
                err.code === "CONFIG_PARSE_ERROR"
              ? 500
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

const VALUE_TYPE_CODE_TO_NAME: Record<number, string> = {
  0: "default",
  1: "bool",
  2: "int",
  3: "decimal",
  4: "string",
  5: "json",
};

function formatRow(row: Record<string, unknown>): SystemConfigResponse {
  const vtCode = row.value_type as number;
  const stCode = row.status as number;
  const { readSystemConfig: _r, makeSupabaseFetcher: _m, ...rest } = {
    readSystemConfig: null,
    makeSupabaseFetcher: null,
    ...row,
  };
  void _r;
  void _m;
  void rest;

  // parse value inline (same logic as parseSystemConfigValue)
  let parsed: unknown = row.config_value;
  try {
    if (vtCode === 1)
      parsed = row.config_value === "true";
    else if (vtCode === 2)
      parsed = parseInt(row.config_value as string, 10);
    else if (vtCode === 3)
      parsed = parseFloat(row.config_value as string);
    else if (vtCode === 5)
      parsed = JSON.parse(row.config_value as string);
  } catch {
    parsed = row.config_value;
  }

  return {
    id: row.id as string,
    domain: row.domain as string,
    config_key: row.config_key as string,
    environment: row.environment as string,
    status: stCode === 1 ? "active" : "disabled",
    status_code: stCode,
    value_type: (VALUE_TYPE_CODE_TO_NAME[vtCode] ?? "default") as never,
    value_type_code: vtCode,
    config_value: row.config_value as string,
    parsed_value: parsed,
    description: row.description as string,
    created_at: row.created_at as string,
    updated_at: row.updated_at as string,
  };
}

// GET /api/admin/configs?environment=&domain=&status=&q=
export async function GET(req: NextRequest) {
  if (!checkAdmin(req))
    return NextResponse.json(
      { error: { code: "CONFIG_UNAUTHORIZED", message: "unauthorized" } },
      { status: 401 },
    );

  try {
    const supabase = createServiceClient();
    const url = new URL(req.url);
    const environmentParam = url.searchParams.get("environment");
    const defaultEnvironment = readStarcoplayConfigEnvironment();
    const environment =
      environmentParam === "*"
        ? null
        : environmentParam
          ? readStarcoplayConfigEnvironment(environmentParam)
          : defaultEnvironment;
    const domain = url.searchParams.get("domain");
    const status = url.searchParams.get("status");
    const q = url.searchParams.get("q");

    let query = supabase.from("system_configs").select("*");

    if (environment) query = query.eq("environment", environment) as typeof query;
    if (domain) query = query.eq("domain", domain) as typeof query;
    if (status === "active") query = query.eq("status", 1) as typeof query;
    else if (status === "disabled") query = query.eq("status", 0) as typeof query;
    if (q) {
      query = query.or(
        `domain.ilike.%${q}%,config_key.ilike.%${q}%,description.ilike.%${q}%`,
      ) as typeof query;
    }

    query = query.order("updated_at", { ascending: false }) as typeof query;

    const { data, error } = await query;
    if (error) throw new ConfigError("CONFIG_SERVICE_ERROR", error.message);

    return NextResponse.json({
      default_environment: defaultEnvironment,
      configs: (data ?? []).map(formatRow),
    });
  } catch (err) {
    return configError(err);
  }
}

// POST /api/admin/configs
export async function POST(req: NextRequest) {
  if (!checkAdmin(req))
    return NextResponse.json(
      { error: { code: "CONFIG_UNAUTHORIZED", message: "unauthorized" } },
      { status: 401 },
    );

  try {
    const body = {
      environment: readStarcoplayConfigEnvironment(),
      ...(await req.json()),
    };
    const validation = validateSystemConfigPayload(body);
    if (!validation.ok) return configError(validation.error);

    const supabase = createServiceClient();
    const { data, error } = await supabase
      .from("system_configs")
      .insert(validation.value)
      .select()
      .single();

    if (error) {
      if (error.code === "23505")
        throw new ConfigError(
          "CONFIG_ALREADY_EXISTS",
          "a config with this domain/config_key/environment already exists",
        );
      throw new ConfigError("CONFIG_SERVICE_ERROR", error.message);
    }

    return NextResponse.json(formatRow(data), { status: 201 });
  } catch (err) {
    return configError(err);
  }
}
