/**
 * PATCH /api/waitlist/update
 *
 * 允许已登录的 guest 用户更新自己的 Waitlist 申请信息。
 *
 * 流程：
 *   1. 验证登录态（双轨鉴权）
 *   2. 查 profiles 表获取 user_type + waitlist_id
 *   3. 权限校验：user_type 必须是 'guest'
 *   4. waitlist_id 必须存在
 *   5. 从请求体读取可更新字段（message / wechat / qq）
 *   6. UPDATE waitlist SET ... WHERE id = waitlist_id
 *   7. 返回更新后的记录
 */

import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { createServiceClient } from "@/lib/supabase-server";

/** 允许 guest 用户更新的 waitlist 字段白名单 */
const UPDATABLE_FIELDS = ["message", "wechat", "qq"] as const;
type UpdatableField = (typeof UPDATABLE_FIELDS)[number];

export async function PATCH(req: NextRequest) {
  // 1. 验证登录态
  const user = await authenticateRequest(req);
  if (!user) {
    return NextResponse.json(
      { error: "unauthorized", reason: "invalid_token" },
      { status: 401 },
    );
  }

  // 2. 查 profiles 表获取 user_type + waitlist_id
  const supabase = createServiceClient();

  const { data: profile, error: profileErr } = await supabase
    .from("profiles")
    .select("user_type, waitlist_id")
    .eq("user_id", user.user_id)
    .maybeSingle();

  if (profileErr) {
    console.error("[waitlist/update] profiles query error", profileErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  if (!profile) {
    return NextResponse.json(
      { error: "profile_not_found", reason: "no profile for current user" },
      { status: 404 },
    );
  }

  // 3. 权限校验：仅 guest 可更新
  if (profile.user_type !== "guest") {
    return NextResponse.json(
      { error: "forbidden", reason: "only guest users can update waitlist" },
      { status: 403 },
    );
  }

  // 4. waitlist_id 必须存在
  if (!profile.waitlist_id) {
    return NextResponse.json(
      { error: "not_found", reason: "no waitlist entry linked to this user" },
      { status: 404 },
    );
  }

  // 5. 解析请求体，白名单过滤
  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const updates: Partial<Record<UpdatableField, string | null>> = {};
  for (const field of UPDATABLE_FIELDS) {
    if (field in body) {
      const val = body[field];
      if (val === null || val === undefined) {
        updates[field] = null;
      } else if (typeof val === "string") {
        updates[field] = val.trim() || null;
      }
      // 非 string/null 的值静默忽略
    }
  }

  if (Object.keys(updates).length === 0) {
    return NextResponse.json(
      { error: "no_valid_fields", reason: "provide at least one of: message, wechat, qq" },
      { status: 400 },
    );
  }

  // 6. UPDATE waitlist
  const { data: updated, error: updateErr } = await supabase
    .from("waitlist")
    .update(updates)
    .eq("id", profile.waitlist_id)
    .select("id, email, wechat, qq, message, status, created_at")
    .single();

  if (updateErr) {
    console.error("[waitlist/update] update error", updateErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  // 7. 返回更新后的记录
  return NextResponse.json({ success: true, waitlist: updated });
}
