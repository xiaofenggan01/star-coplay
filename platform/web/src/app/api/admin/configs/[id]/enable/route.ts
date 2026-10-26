import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";
import { ConfigError } from "@/lib/system-config";

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
      : err.code === "CONFIG_UNAUTHORIZED" ? 401
      : err.code === "CONFIG_SERVICE_ERROR" ? 500
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

// POST /api/admin/configs/[id]/enable
export async function POST(
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
    const supabase = createServiceClient();

    // Fetch current record to validate config_value is parseable before enabling
    const { data: existing, error: fetchErr } = await supabase
      .from("system_configs")
      .select("*")
      .eq("id", id)
      .single();

    if (fetchErr || !existing)
      throw new ConfigError("CONFIG_NOT_FOUND", "config not found");

    // Validate the stored value is parseable before enabling
    const { parseSystemConfigValue } = await import("@/lib/system-config");
    try {
      parseSystemConfigValue(existing.value_type, existing.config_value);
    } catch {
      throw new ConfigError(
        "CONFIG_PARSE_ERROR",
        `cannot enable: stored config_value cannot be parsed as ${existing.value_type}`,
      );
    }

    const { data, error } = await supabase
      .from("system_configs")
      .update({ status: 1 })
      .eq("id", id)
      .select()
      .single();

    if (error)
      throw new ConfigError("CONFIG_SERVICE_ERROR", error.message);

    return NextResponse.json({ id: data.id, status: "active", status_code: 1 });
  } catch (err) {
    return configError(err);
  }
}
