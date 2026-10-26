/**
 * 阶段四 A4a：Token 找回端点已下线。
 *
 * 原因：旧端点允许匿名用户通过昵称/邮箱/手机号反查 platform_token，
 * 在引入 Supabase Auth 后属于严重账号枚举/接管漏洞。
 *
 * 替代方案：
 *   - 已绑定邮箱的账号 → POST /api/auth/reset-password（待 A4b 实现）
 *   - 未绑定邮箱的老用户 → 走人工客服恢复流程
 *
 * 兼容性：保留路由但返回 410 Gone，告知客户端端点已永久下线。
 */

import { NextResponse } from "next/server";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

export async function POST() {
  return NextResponse.json(
    {
      error: "endpoint_deprecated",
      message: "Token 找回功能已下线，请通过邮箱重置密码",
      moved_to: "/api/auth/reset-password",
    },
    { status: 410, headers: corsHeaders },
  );
}
