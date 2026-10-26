/**
 * /api/register
 *
 * 阶段四（A4b）：接入 Supabase Auth（service role + admin.createUser）。
 *   - 旧版（A3c）从 lib/invite-code.ts 校验邀请码后直接 INSERT public.users + 自生成 platform_token
 *   - 新版改为：consume 邀请码 → admin.createUser → trigger 自动建 public.users → 反查取 user_id
 *   - 严禁改用 supabase.auth.signUp（绕过本接口的邀请码 CAS）
 *
 * 业务核心 / 依赖注入接口 / 默认 deps 全部在 lib/register-handler.ts，
 * 本文件仅做 HTTP 边界翻译（解析 body / 基础校验 / 调 handler / 包装 NextResponse）。
 * 这样切的原因：Next.js 16 的 route.ts 不允许导出非 HTTP-handler 名字。
 */

import { NextRequest, NextResponse } from "next/server";
import {
  buildDefaultDeps,
  handleRegister,
  type RegisterRequestBody,
} from "@/lib/register-handler";
import {
  validateEmailOptional,
  normalizeEmailForSubmit,
} from "@/lib/register-validation";

export async function POST(req: NextRequest) {
  const raw = await req.text();
  let body: RegisterRequestBody;
  try {
    body = JSON.parse(raw) as RegisterRequestBody;
  } catch {
    return NextResponse.json({ error: "invalid json" }, { status: 400 });
  }

  // HTTP 边界级必填校验：handler 内也会兜底返回 400，但在这里提前给出更友好的字段级错误
  const emailRaw = (body.email ?? "").trim();
  if (!emailRaw) {
    return NextResponse.json(
      { error: "email required", field: "email" },
      { status: 400 },
    );
  }
  const emailErr = validateEmailOptional(emailRaw);
  if (emailErr) {
    return NextResponse.json({ error: emailErr, field: "email" }, { status: 400 });
  }
  body.email = normalizeEmailForSubmit(emailRaw);

  const password = body.password ?? "";
  if (!password) {
    return NextResponse.json(
      { error: "password required", field: "password" },
      { status: 400 },
    );
  }
  if (password.length < 8) {
    return NextResponse.json(
      { error: "密码长度至少 8 位", field: "password" },
      { status: 400 },
    );
  }

  const result = await handleRegister(buildDefaultDeps(), body);
  return NextResponse.json(result.body, { status: result.status });
}
