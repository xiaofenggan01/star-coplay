import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";
import { checkRateLimit } from "@/lib/rate-limit";
import { escapeOrValue, escapeLikeWildcards } from "@/lib/waitlist-query";

/**
 * POST /api/waitlist/check
 *
 * 内测资格校验：用户输入联系方式（email/wechat/qq 任一），
 * 服务端按精确匹配查询 waitlist；命中且 status=approved 即返回邀请码。
 *
 * 关键安全设计（参见 tech-spec.md §5.4）：
 *   - 精确匹配，不用 LIKE/子串（防止枚举攻击）
 *   - email 大小写不敏感（ilike 全字符串），wechat/qq eq 严格相等
 *   - 同 IP 5/min 限流，超出返回 429 + Retry-After
 *   - 成功/失败响应统一走 200ms 等延迟（防时序攻击区分命中与否）
 *   - invite_code 来源：环境变量 OFFICIAL_BETA_INVITE_CODE（阶段三才迁到 invite_codes 表）
 *
 * 请求体：{ email?: string; wechat?: string; qq?: string }（至少一个非空）
 * 响应：
 *   200 命中 → { matched: true, waitlist_id, invite_code }
 *   200 未命中 → { matched: false, status: 'not_found' | 'not_approved' }
 *   400 参数错误 → { error: 'invalid_request', message }
 *   429 限流 → { error: 'rate_limited' } + Retry-After: 60
 *   503 缺配置 → { error: 'invite_code_unavailable' }
 *   500 内部错误 → { error: 'internal_error' }
 */

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization",
};

const RATE_LIMIT_PER_MIN = Number(process.env.WAITLIST_CHECK_RATE_LIMIT ?? "5") || 5;
const EQUAL_RESPONSE_DELAY_MS = 200;

export async function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: corsHeaders });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 从 x-forwarded-for 取真实客户端 IP（Vercel 会在请求头注入） */
function clientIp(req: NextRequest): string {
  const xff = req.headers.get("x-forwarded-for");
  if (xff) {
    const first = xff.split(",")[0]?.trim();
    if (first) return first;
  }
  const realIp = req.headers.get("x-real-ip");
  if (realIp) return realIp.trim();
  return "unknown";
}

function jsonWith(
  body: Record<string, unknown>,
  init: { status?: number; extraHeaders?: Record<string, string> } = {},
) {
  const headers: Record<string, string> = { ...corsHeaders };
  if (init.extraHeaders) Object.assign(headers, init.extraHeaders);
  return NextResponse.json(body, { status: init.status ?? 200, headers });
}

export async function POST(req: NextRequest) {
  // 1. 限流（基于 IP）— 限流命中也走等延迟，避免泄漏"是否被限流"以外的信息
  const ip = clientIp(req);
  const allowed = checkRateLimit(`waitlist-check:${ip}`, RATE_LIMIT_PER_MIN);
  if (!allowed) {
    return jsonWith(
      { error: "rate_limited", message: "请求过于频繁，请稍后再试" },
      { status: 429, extraHeaders: { "Retry-After": "60" } },
    );
  }

  // 2. 解析请求体
  let body: { email?: string; wechat?: string; qq?: string };
  try {
    body = await req.json();
  } catch {
    return jsonWith({ error: "invalid_request", message: "请求体不是合法 JSON" }, { status: 400 });
  }

  const email = (body.email ?? "").trim();
  const wechat = (body.wechat ?? "").trim();
  const qq = (body.qq ?? "").trim();

  if (!email && !wechat && !qq) {
    return jsonWith(
      { error: "invalid_request", message: "至少提供 email / wechat / qq 之一" },
      { status: 400 },
    );
  }

  // 3. 启动等延迟计时器（与查询并发，最终统一等齐 200ms 再返回）
  const delayPromise = sleep(EQUAL_RESPONSE_DELAY_MS);

  try {
    const supabase = createServiceClient();

    // 4. 精确匹配查询：构造 OR 条件
    //    - email: ilike 完整字符串（大小写不敏感）
    //    - wechat / qq: eq 严格相等
    //    Supabase JS 用 .or('a.eq.x,b.eq.y') 表达 OR
    //    注意：值不能含逗号（联系方式无此场景），且应避免特殊字符注入
    //    这里值已 trim，不做更严格的字符校验（DB 参数化查询本身安全）
    const orParts: string[] = [];
    if (email) {
      // ilike 在 PG 中 % / _ 是通配符，必须转义否则攻击者输入 %@%.com 可枚举所有 approved 邮箱
      // 用 \ 转义（PG ILIKE 默认 ESCAPE 字符）后，再走 PostgREST 字符过滤
      orParts.push(`email.ilike.${escapeOrValue(escapeLikeWildcards(email))}`);
    }
    if (wechat) orParts.push(`wechat.eq.${escapeOrValue(wechat)}`);
    if (qq) orParts.push(`qq.eq.${escapeOrValue(qq)}`);

    const { data, error } = await supabase
      .from("waitlist")
      .select("id, status, assigned_invite_code")
      .or(orParts.join(","))
      .limit(1)
      .maybeSingle();

    if (error) {
      // 查询出错：等延迟后返回 500，仍不泄漏命中状态
      await delayPromise;
      console.error("[waitlist/check] db error", error);
      return jsonWith({ error: "internal_error" }, { status: 500 });
    }

    // 5. 处理命中/未命中
    if (!data) {
      await delayPromise;
      return jsonWith({ matched: false, status: "not_found" });
    }

    if (data.status !== "approved") {
      await delayPromise;
      return jsonWith({ matched: false, status: "not_approved" });
    }

    // 命中且 approved：优先使用 assigned_invite_code（管理员指定的码）
    let resolvedCode: string | null = null;

    if (data.assigned_invite_code) {
      // 验证指定码仍然有效（未禁用、未过期、未耗尽）
      const { data: assignedRow, error: assignedErr } = await supabase
        .from("invite_codes")
        .select("code, disabled, expires_at, used_count, max_uses")
        .eq("code", data.assigned_invite_code)
        .maybeSingle();

      if (!assignedErr && assignedRow && !assignedRow.disabled) {
        const notExpired = !assignedRow.expires_at || new Date(assignedRow.expires_at) > new Date();
        const notExhausted = assignedRow.used_count < assignedRow.max_uses;
        if (notExpired && notExhausted) {
          resolvedCode = assignedRow.code as string;
        }
      }
    }

    // 回退：从 invite_codes 表取当前生效的"内测共享码"
    if (!resolvedCode) {
      const { data: inviteRow, error: inviteErr } = await supabase
        .from("invite_codes")
        .select("code")
        .like("official_code_reason", "%beta-channel")
        .eq("disabled", false)
        .or("expires_at.is.null,expires_at.gt.now()")
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle();

      if (inviteErr || !inviteRow) {
        await delayPromise;
        console.error(
          "[waitlist/check] no active beta-channel invite code in invite_codes table",
          inviteErr?.message ?? "(no matching row)",
        );
        return jsonWith(
          { error: "invite_code_unavailable", message: "系统暂未配置内测邀请码" },
          { status: 503 },
        );
      }
      resolvedCode = inviteRow.code as string;
    }

    await delayPromise;
    return jsonWith({
      matched: true,
      waitlist_id: String(data.id),
      invite_code: resolvedCode,
    });
  } catch (err) {
    await delayPromise;
    const msg = err instanceof Error ? err.message : "internal error";
    console.error("[waitlist/check] unexpected", msg);
    return jsonWith({ error: "internal_error" }, { status: 500 });
  }
}

/**
 * 转义 PostgREST .or() 参数中的特殊字符。
 * PostgREST 的 or filter 用逗号分隔多个条件，值中若含逗号会破坏解析。
 * 联系方式（email/wechat/qq）正常不会有逗号，但仍做防御性处理。
 * 同时移除括号防止 .or() 嵌套语法误解析。
 */
