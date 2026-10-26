/**
 * POST /api/auth/activate
 *
 * 新版 Waitlist 邀请注册流程的激活端点。
 *
 * 流程：
 *   1. 校验 token + password
 *   2. 查 verification_tokens，检查有效性（不存在 / 已用 / 已过期）
 *   3. 解析邀请码（assigned_invite_code 或 fallback beta-channel）
 *   4. 若邮箱已有账号，按现有账号路径升级/开通
 *   5. 新账号路径 consumeInviteCode 后 admin.createUser（失败则 refund）
 *   6. 标记 token used_at
 *   7. 调 openPlatformAccess 开通资源并生成用户邀请码
 *   8. 更新 waitlist status → 'registered'
 *   9. 返回 { user_id, email, resource_status }，前端自行 signInWithPassword 登录
 *
 * 注意：session 由前端负责，服务端不处理 cookie/session。
 */

import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";
import { consumeInviteCode, refundInviteCode } from "@/lib/invite-code";
import { openPlatformAccess } from "@/lib/platform-access-open";

// 与 set-password/page.tsx 中的 validatePassword 保持一致
function validatePassword(raw: string): string | null {
  if (!raw) return "请输入密码";
  if (raw.length < 8) return "密码至少 8 位";
  if (!/[A-Za-z]/.test(raw)) return "密码需包含字母";
  if (!/\d/.test(raw)) return "密码需包含数字";
  return null;
}

interface ActivateRequestBody {
  token: string;
  password: string;
}

interface VerificationTokenRow {
  token: string;
  email: string;
  type: string;
  waitlist_id: number | null;
  expires_at: string;
  used_at: string | null;
}

interface PublicUserRow {
  user_id: string;
}

export async function POST(req: NextRequest) {
  // 1. 解析 body
  let body: ActivateRequestBody;
  try {
    body = (await req.json()) as ActivateRequestBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const token = (body.token ?? "").trim();
  const password = body.password ?? "";

  // 2. 基础校验
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

  // 3. 查 verification_tokens
  const { data: tokenRow, error: tokenQueryErr } = await supabase
    .from("verification_tokens")
    .select("token, email, type, waitlist_id, expires_at, used_at")
    .eq("token", token)
    .maybeSingle<VerificationTokenRow>();

  if (tokenQueryErr) {
    console.error("[activate] token query error", tokenQueryErr);
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

  const { email, waitlist_id } = tokenRow;

  // 4. 解析该用户应使用的邀请码（assigned_invite_code 或 fallback beta-channel）
  let inviteCode: string | null = null;

  if (waitlist_id !== null) {
    const { data: wlRow } = await supabase
      .from("waitlist")
      .select("assigned_invite_code")
      .eq("id", waitlist_id)
      .maybeSingle();

    if (wlRow?.assigned_invite_code) {
      inviteCode = wlRow.assigned_invite_code;
    }
  }

  if (!inviteCode) {
    const { data: fallbackRow } = await supabase
      .from("invite_codes")
      .select("code")
      .like("official_code_reason", "%beta-channel")
      .eq("disabled", false)
      .or("expires_at.is.null,expires_at.gt.now()")
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();

    if (fallbackRow) {
      inviteCode = fallbackRow.code;
    }
  }

  if (!inviteCode) {
    console.error("[activate] no available invite code for", email);
    return NextResponse.json(
      { error: "no_invite_code_available" },
      { status: 503 },
    );
  }

  // 5. 已有账号路径先处理，避免 createUser 失败后再 refund + re-consume。
  const { data: existingUserBeforeCreate, error: existingUserBeforeCreateErr } = await supabase
    .from("users")
    .select("user_id, auth_id")
    .eq("email", email)
    .not("auth_id", "is", null)
    .maybeSingle();

  if (existingUserBeforeCreateErr) {
    console.error("[activate] existing user precheck error", existingUserBeforeCreateErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  if (existingUserBeforeCreate) {
    const { data: profileRow, error: profileErr } = await supabase
      .from("profiles")
      .select("user_type, waitlist_id")
      .eq("user_id", existingUserBeforeCreate.user_id)
      .maybeSingle();

    if (profileErr) {
      console.error("[activate] profile query error", profileErr);
      return NextResponse.json({ error: "internal_error" }, { status: 500 });
    }

    const currentType = profileRow?.user_type ?? "guest";
    const upgradeWaitlistId = profileRow?.waitlist_id ?? waitlist_id;
    let existingRelationInviterId: string | null = null;

    if (currentType === "guest") {
      const existingConsumeRes = await consumeInviteCode(inviteCode);
      if (!existingConsumeRes.ok) {
        console.error("[activate] consumeInviteCode failed", inviteCode, existingConsumeRes.reason);
        return NextResponse.json(
          { error: `invite_code_${existingConsumeRes.reason}` },
          { status: 422 },
        );
      }

      if (existingUserBeforeCreate.auth_id) {
        const { error: pwdUpdateErr } = await supabase.auth.admin.updateUserById(
          existingUserBeforeCreate.auth_id,
          { password },
        );
        if (pwdUpdateErr) {
          await refundInviteCode(inviteCode);
          console.warn("[activate] password update failed (upgrade path)", pwdUpdateErr);
          return NextResponse.json({ error: "internal_error" }, { status: 500 });
        }
      }

      existingRelationInviterId =
        existingConsumeRes.code.type === "user" &&
        existingConsumeRes.code.owner_user_id &&
        existingConsumeRes.code.owner_user_id !== existingUserBeforeCreate.user_id
          ? existingConsumeRes.code.owner_user_id
          : null;
    }

    if (upgradeWaitlistId !== null) {
      const { error: wlErr } = await supabase
        .from("waitlist")
        .update({ status: "registered" })
        .eq("id", upgradeWaitlistId)
        .in("status", ["approved", "pending", "registered"]);

      if (wlErr) {
        console.warn("[activate] waitlist status update failed (existing account path)", wlErr);
      }
    }

    const { error: markUsedErr } = await supabase
      .from("verification_tokens")
      .update({ used_at: new Date().toISOString() })
      .eq("token", token);

    if (markUsedErr) {
      console.error("[activate] failed to mark token used (existing account path)", markUsedErr);
    }

    const openResult = await openPlatformAccess(
      {
        user_id: existingUserBeforeCreate.user_id,
        email,
        display_name: email,
        waitlist_id: upgradeWaitlistId,
        inviter_user_id: existingRelationInviterId,
        invite_code: currentType === "guest" ? inviteCode : null,
        source: currentType === "guest" ? "activate_guest_upgrade" : "activate_existing_account",
      },
      supabase,
    );

    return NextResponse.json(
      {
        ...(currentType !== "guest" ? { already_activated: true, message: "账号已激活" } : {}),
        user_id: existingUserBeforeCreate.user_id,
        email,
        resource_status: "key_enabled",
        open_access: openResult,
      },
      { status: 200 },
    );
  }

  const consumeRes = await consumeInviteCode(inviteCode);
  if (!consumeRes.ok) {
    console.error("[activate] consumeInviteCode failed", inviteCode, consumeRes.reason);
    return NextResponse.json(
      { error: `invite_code_${consumeRes.reason}` },
      { status: 422 },
    );
  }

  // 6. 创建 Supabase Auth 账号
  const { data: createData, error: createErr } =
    await supabase.auth.admin.createUser({
      email,
      password,
      email_confirm: true,
      user_metadata: { invite_code: inviteCode, user_type: "guest" },
    });

  if (createErr) {
    // createUser 失败，先退还邀请码
    await refundInviteCode(inviteCode);

    const msg = createErr.message ?? "";
    const isEmailConflict =
      msg.includes("email_exists") ||
      msg.includes("user_already_exists") ||
      msg.includes("already been registered") ||
      createErr.code === "email_exists" ||
      createErr.code === "user_already_exists";

    if (isEmailConflict) {
      console.warn("[activate] createUser email conflict after precheck", createErr);
      return NextResponse.json(
        { error: "email_already_registered" },
        { status: 409 },
      );
    }

    console.error("[activate] createUser error", createErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  const authId = createData.user?.id;
  if (!authId) {
    console.error("[activate] createUser returned no user id");
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  // 7. 标记 token 已使用
  const { error: markErr } = await supabase
    .from("verification_tokens")
    .update({ used_at: new Date().toISOString() })
    .eq("token", token);

  if (markErr) {
    // 非致命，记录日志但继续
    console.error("[activate] failed to mark token used", markErr);
  }

  // 8. 最佳努力：更新 waitlist status → 'registered'
  if (waitlist_id !== null) {
    const { error: waitlistErr } = await supabase
      .from("waitlist")
      .update({ status: "registered" })
      .eq("id", waitlist_id)
      .eq("status", "approved");

    if (waitlistErr) {
      console.warn("[activate] waitlist status update failed", waitlistErr);
    }
  }

  // 9. 反查 public.users 取 user_id
  // trigger 在 createUser 后同步执行，通常立即可查；若未就绪则短暂等待
  let userRow: PublicUserRow | null = null;
  for (let attempt = 0; attempt < 3; attempt++) {
    const { data, error: userQueryErr } = await supabase
      .from("users")
      .select("user_id")
      .eq("auth_id", authId)
      .maybeSingle<PublicUserRow>();

    if (userQueryErr) {
      console.error("[activate] users query error", userQueryErr);
      break;
    }
    if (data) {
      userRow = data;
      break;
    }
    // trigger 可能有轻微延迟，等 200ms 再试
    await new Promise((r) => setTimeout(r, 200));
  }

  if (!userRow) {
    // Auth 账号已建，但 public.users 行不存在（trigger 未触发或失败）
    console.error("[activate] public.users row not found for auth_id", authId);
    return NextResponse.json(
      { error: "user_profile_not_found" },
      { status: 500 },
    );
  }

  const openResult = await openPlatformAccess(
    {
      user_id: userRow.user_id,
      email,
      display_name: email,
      waitlist_id,
      inviter_user_id:
        consumeRes.code.type === "user" &&
        consumeRes.code.owner_user_id &&
        consumeRes.code.owner_user_id !== userRow.user_id
          ? consumeRes.code.owner_user_id
          : null,
      invite_code: inviteCode,
      source: "activate_new_user",
    },
    supabase,
  );

  return NextResponse.json(
    {
      user_id: userRow.user_id,
      email,
      resource_status: "key_enabled",
      open_access: openResult,
    },
    { status: 200 },
  );
}
