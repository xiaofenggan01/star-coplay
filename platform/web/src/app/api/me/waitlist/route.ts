/**
 * GET /api/me/waitlist
 *
 * 返回当前登录用户关联的 Waitlist 申请记录。
 *
 * 流程：
 *   1. 双轨鉴权
 *   2. 查 profiles 表获取 waitlist_id
 *   3. 如果 waitlist_id 存在，查 waitlist 表返回记录
 *   4. 否则返回 null
 */

import { NextRequest, NextResponse } from "next/server";
import { authenticateRequest } from "@/lib/auth";
import { createServiceClient } from "@/lib/supabase-server";

export async function GET(req: NextRequest) {
  // 1. 验证登录态
  const user = await authenticateRequest(req);
  if (!user) {
    return NextResponse.json(
      { error: "unauthorized", reason: "invalid_token" },
      { status: 401 },
    );
  }

  const supabase = createServiceClient();

  // 2. 查 profiles 表获取 waitlist_id
  const { data: profile, error: profileErr } = await supabase
    .from("profiles")
    .select("user_type, waitlist_id")
    .eq("user_id", user.user_id)
    .maybeSingle();

  if (profileErr) {
    console.error("[me/waitlist] profiles query error", profileErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  if (!profile) {
    return NextResponse.json({ waitlist: null });
  }

  // 非 guest 用户或无 waitlist_id 时返回 null
  if (profile.user_type !== "guest" || !profile.waitlist_id) {
    return NextResponse.json({ waitlist: null });
  }

  // 3. 查 waitlist 表
  const { data: waitlist, error: waitlistErr } = await supabase
    .from("waitlist")
    .select("id, email, wechat, qq, message, status, created_at")
    .eq("id", profile.waitlist_id)
    .maybeSingle();

  if (waitlistErr) {
    console.error("[me/waitlist] waitlist query error", waitlistErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  return NextResponse.json({ waitlist: waitlist ?? null });
}
