import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";
import {
  validateSystemConfigPayload,
  ConfigError,
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
      err.code === "CONFIG_NOT_FOUND" ? 404
      : err.code === "CONFIG_DISABLED" || err.code === "CONFIG_ALREADY_EXISTS" ? 409
      : err.code === "CONFIG_UNAUTHORIZED" ? 401
      : err.code === "CONFIG_SERVICE_ERROR" || err.code === "CONFIG_PARSE_ERROR" ? 500
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
  0: "default", 1: "bool", 2: "int", 3: "decimal", 4: "string", 5: "json",
};

function parsedValue(vtCode: number, configValue: string): unknown {
  try {
    if (vtCode === 1) return configValue === "true";
    if (vtCode === 2) return parseInt(configValue, 10);
    if (vtCode === 3) return parseFloat(configValue);
    if (vtCode === 5) return JSON.parse(configValue);
  } catch { /* fall through */ }
  return configValue;
}

function formatRow(row: Record<string, unknown>) {
  const vtCode = row.value_type as number;
  const stCode = row.status as number;
  return {
    id: row.id,
    domain: row.domain,
    config_key: row.config_key,
    environment: row.environment,
    status: stCode === 1 ? "active" : "disabled",
    status_code: stCode,
    value_type: VALUE_TYPE_CODE_TO_NAME[vtCode] ?? "default",
    value_type_code: vtCode,
    config_value: row.config_value,
    parsed_value: parsedValue(vtCode, row.config_value as string),
    description: row.description,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}

// PUT /api/admin/configs/[id]
export async function PUT(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  if (!checkAdmin(req))
    return NextResponse.json(
      { error: { code: "CONFIG_UNAUTHORIZED", message: "unauthorized" } },
      { status: 401 },
    );

  try {
    const { id } = await params;
    const body = await req.json();

    // Only allow updating non-status fields via PUT
    const allowed = ["domain", "config_key", "environment", "value_type", "config_value", "description"];
    const updates: Record<string, unknown> = {};
    for (const key of allowed) {
      if (key in body) updates[key] = body[key];
    }

    if (Object.keys(updates).length === 0)
      throw new ConfigError("CONFIG_SERVICE_ERROR", "no updatable fields provided");

    // If any identity or value field changed, re-validate the full merged payload
    const needsValidation = ["domain", "config_key", "environment", "value_type", "config_value"].some(
      (k) => k in updates,
    );
    if (needsValidation) {
      const supabase = createServiceClient();
      const { data: existing, error: fetchErr } = await supabase
        .from("system_configs")
        .select("*")
        .eq("id", id)
        .single();
      if (fetchErr || !existing)
        throw new ConfigError("CONFIG_NOT_FOUND", "config not found");

      const merged = {
        domain: updates.domain ?? existing.domain,
        config_key: updates.config_key ?? existing.config_key,
        environment: updates.environment ?? existing.environment,
        value_type: updates.value_type ?? VALUE_TYPE_CODE_TO_NAME[existing.value_type as number],
        config_value: updates.config_value ?? existing.config_value,
        description: updates.description ?? existing.description,
      };
      const validation = validateSystemConfigPayload(merged);
      if (!validation.ok) return configError(validation.error);
      updates.value_type = validation.value.value_type;
      updates.config_value = validation.value.config_value;
    }

    const supabase = createServiceClient();
    const { data, error } = await supabase
      .from("system_configs")
      .update(updates)
      .eq("id", id)
      .select()
      .single();

    if (error) {
      if (error.code === "23505")
        throw new ConfigError("CONFIG_ALREADY_EXISTS", "duplicate domain/config_key/environment");
      if (error.code === "PGRST116")
        throw new ConfigError("CONFIG_NOT_FOUND", "config not found");
      throw new ConfigError("CONFIG_SERVICE_ERROR", error.message);
    }

    return NextResponse.json(formatRow(data));
  } catch (err) {
    return configError(err);
  }
}
