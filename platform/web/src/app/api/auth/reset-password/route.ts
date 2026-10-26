/**
 * POST /api/auth/reset-password
 *
 * 密码重置执行端点。
 *
 * 流程：
 *   1. 接收 token + password
 *   2. 校验密码强度
 *   3. 查 verification_tokens 验证有效性（不存在 / 已用 / 已过期）
 *   4. 用 email 查 auth.users 获取 user id
 *   5. admin.updateUserById 更新密码
 *   6. 标记 token used_at
 *   7. 返回 { email }
 */

import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";

function validatePassword(raw: string): string | null {
  if (!raw) return "请输入密码";
  if (raw.length < 8) return "密码至少 8 位";
  if (!/[A-Za-z]/.test(raw)) return "密码需包含字母";
  if (!/\d/.test(raw)) return "密码需包含数字";
  return null;
}

interface ResetRequestBody {
  token: string;
  password: string;
}

interface VerificationTokenRow {
  token: string;
  email: string;
  type: string;
  expires_at: string;
  used_at: string | null;
}

export async function POST(req: NextRequest) {
  let body: ResetRequestBody;
  try {
    body = (await req.json()) as ResetRequestBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const token = (body.token ?? "").trim();
  const password = body.password ?? "";

  if (!token) {
    return NextResponse.json(
      { error: "token_required", field: "token" },
      { status: 400 },
    );
  }

  const pwdErr = validatePassword(password);
  if (pwdErr) {
    return NextResponse.json(
      { error: pwdErr, field: "password" },
      { status: 400 },
    );
  }

  const supabase = createServiceClient();

  // 查 verification_tokens
  const { data: tokenRow, error: tokenQueryErr } = await supabase
    .from("verification_tokens")
    .select("token, email, type, expires_at, used_at")
    .eq("token", token)
    .eq("type", "password_reset")
    .maybeSingle<VerificationTokenRow>();

  if (tokenQueryErr) {
    console.error("[reset-password] token query error", tokenQueryErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  if (!tokenRow) {
    return NextResponse.json({ error: "token_not_found" }, { status: 404 });
  }

  if (tokenRow.used_at !== null) {
    return NextResponse.json({ error: "token_already_used" }, { status: 409 });
  }

  if (new Date(tokenRow.expires_at) < new Date()) {
    return NextResponse.json({ error: "token_expired" }, { status: 410 });
  }

  const { email } = tokenRow;

  // 用 email 查 public.users 获取 auth_id
  const { data: userRow, error: userErr } = await supabase
    .from("users")
    .select("auth_id")
    .eq("email", email)
    .not("auth_id", "is", null)
    .maybeSingle();

  if (userErr) {
    console.error("[reset-password] users query error", userErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  if (!userRow) {
    console.error("[reset-password] user not found for email", email);
    return NextResponse.json({ error: "user_not_found" }, { status: 404 });
  }

  // 更新密码
  const { error: updateErr } = await supabase.auth.admin.updateUserById(
    userRow.auth_id,
    { password },
  );

  if (updateErr) {
    console.error("[reset-password] updateUserById error", updateErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  // 标记 token 已使用
  const { error: markErr } = await supabase
    .from("verification_tokens")
    .update({ used_at: new Date().toISOString() })
    .eq("token", token);

  if (markErr) {
    console.error("[reset-password] failed to mark token used", markErr);
  }

  return NextResponse.json({ email }, { status: 200 });
}
