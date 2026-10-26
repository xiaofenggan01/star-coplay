/**
 * POST /api/auth/forgot-password
 *
 * 密码重置请求端点。
 *
 * 流程：
 *   1. 接收 email，trim + lowercase
 *   2. 频率限制：5 分钟内同邮箱已有未使用的 password_reset token → 静默返回成功
 *   3. 确认邮箱在 auth.users 中存在（不存在也返回统一提示，防枚举）
 *   4. 作废同邮箱旧的未使用 password_reset token
 *   5. 生成新 token，expires_at = 1 小时后
 *   6. 用 Resend 发送重置邮件
 *   7. 统一返回成功提示
 */

import { NextRequest, NextResponse } from "next/server";
import { createServiceClient } from "@/lib/supabase-server";

const RATE_LIMIT_MINUTES = 5;

const RESET_EMAIL_HTML = `
<!-- ============ 邮件 HTML 开始（③ 密码重置） ============ -->
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#fdf6e3;font-family:'Courier New',Courier,'PingFang SC','Microsoft YaHei',sans-serif;color:#6b4226;">
  <tr>
    <td align="center" style="padding:0;">

      <!-- Hero Banner（红橙警示渐变） -->
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;background:#D64045;background:linear-gradient(180deg,#8b3a1f 0%,#D64045 50%,#f4c58e 100%);">
        <tr>
          <td align="center" style="padding:32px 20px 24px 20px;">
            <img src="https://star-coplay.luosea.cn/logo-new.png" alt="Star CoPlay" width="220" height="auto" style="display:block;max-width:80%;height:auto;border:0;outline:none;text-decoration:none;filter:drop-shadow(0 2px 4px rgba(0,0,0,0.4));">
            <p style="margin:14px 0 0 0;font-family:'Courier New',monospace;font-size:13px;color:#ffffff;letter-spacing:1px;text-shadow:0 1px 2px rgba(0,0,0,0.4);">星 露 谷 中 永 远 在 线 的 搭 子</p>
          </td>
        </tr>
      </table>

      <!-- Parchment Card -->
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">
        <tr>
          <td style="padding:24px 24px 0 24px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f5e6c8;background:linear-gradient(135deg,#f5e6c8 0%,#eed9a4 50%,#e8cc8c 100%);border:3px solid #8b6914;border-radius:12px;">

              <!-- 顶部安全提示条（前置） -->
              <tr>
                <td style="padding:20px 24px 0 24px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fff4e6;border:2px solid #D64045;border-radius:8px;">
                    <tr>
                      <td style="padding:10px 14px;">
                        <p style="margin:0;font-size:12px;line-height:1.7;color:#8b3a1f;">
                          <strong>⚠ 安全提示：</strong>如果<strong>不是你本人</strong>申请重置密码，请直接<strong>忽略此邮件</strong>，你的账号依然安全。
                        </p>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>

              <tr>
                <td align="center" style="padding:24px 28px 8px 28px;">
                  <img src="https://star-coplay.luosea.cn/assets/icons/Cinder_Shard.png" alt="" width="48" height="48" style="display:block;border:0;outline:none;image-rendering:pixelated;">
                </td>
              </tr>
              <tr>
                <td align="center" style="padding:0 28px 8px 28px;">
                  <h1 style="margin:0;font-family:'Courier New',monospace;font-size:22px;line-height:1.4;color:#5a3e0a;letter-spacing:1px;">重置你的密码</h1>
                </td>
              </tr>
              <tr>
                <td align="center" style="padding:8px 28px 20px 28px;">
                  <p style="margin:0;font-size:14px;line-height:1.8;color:#6b4226;">
                    我们收到了你的密码重置请求。<br>
                    点击下方按钮，设置一个新密码。
                  </p>
                </td>
              </tr>

              <!-- CTA -->
              <tr>
                <td align="center" style="padding:4px 28px 24px 28px;">
                  <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                    <tr>
                      <td align="center" bgcolor="#8b6914" style="border-radius:8px;background:#8b6914;background:linear-gradient(180deg,#c4a35a 0%,#8b6914 100%);border:3px solid #5a3e0a;box-shadow:0 3px 0 #5a3e0a;">
                        <a href="{{CONFIRMATION_URL}}" target="_blank" style="display:inline-block;padding:14px 36px;min-height:24px;font-family:'Courier New',monospace;font-size:15px;font-weight:bold;color:#ffffff;text-decoration:none;letter-spacing:1px;text-shadow:1px 1px 2px rgba(0,0,0,0.5);">✦ 设 置 新 密 码 ✦</a>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>

              <!-- Fallback -->
              <tr>
                <td style="padding:0 28px 20px 28px;">
                  <p style="margin:0 0 6px 0;font-size:12px;color:#8b6914;">如按钮无法点击，请复制以下链接：</p>
                  <p style="margin:0;font-size:12px;line-height:1.6;color:#4a90d9;word-break:break-all;background:#fff;border:1px dashed #c4a35a;padding:8px 10px;border-radius:6px;">{{CONFIRMATION_URL}}</p>
                </td>
              </tr>

              <!-- 链接时效 -->
              <tr>
                <td style="padding:0 28px 28px 28px;">
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#fdf6e3;border:2px solid #c4a35a;border-radius:8px;">
                    <tr>
                      <td style="padding:12px 14px;">
                        <p style="margin:0;font-size:12px;line-height:1.7;color:#6b4226;">
                          <strong style="color:#8b6914;">⏳ 链接时效：</strong>该重置链接 1 小时内有效。<br>
                          <strong style="color:#8b6914;">🔒 账号安全：</strong>建议设置独立的强密码，不要与其他网站共用。
                        </p>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>

      <!-- Footer -->
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;width:100%;">
        <tr>
          <td align="center" style="padding:24px 24px 32px 24px;">
            <p style="margin:0 0 6px 0;font-family:'Courier New',monospace;font-size:11px;color:#8b6914;letter-spacing:0.5px;">— Star CoPlay · AI Co-Player for Stardew Valley —</p>
            <p style="margin:0 0 6px 0;font-size:11px;color:#8b6914;">本邮件由系统自动发送，请勿直接回复。</p>
            <p style="margin:0;font-size:10px;color:#c4a35a;">Powered by Star CoPlay · {{SITE_URL}}</p>
          </td>
        </tr>
      </table>

    </td>
  </tr>
</table>
<!-- ============ 邮件 HTML 结束（③ 密码重置） ============ -->
`.trim();

function buildEmailHtml(confirmationUrl: string, siteUrl: string): string {
  return RESET_EMAIL_HTML
    .replaceAll("{{CONFIRMATION_URL}}", confirmationUrl)
    .replaceAll("{{SITE_URL}}", siteUrl);
}

export async function POST(req: NextRequest) {
  let body: { email?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const email = (body.email ?? "").trim().toLowerCase();
  if (!email) {
    return NextResponse.json(
      { error: "email_required", field: "email" },
      { status: 400 },
    );
  }

  const supabase = createServiceClient();
  const successResponse = NextResponse.json(
    { message: "如果邮箱已注册，将收到重置邮件" },
    { status: 200 },
  );

  // 频率限制：5 分钟内同邮箱已有未使用的 password_reset token
  const fiveMinAgo = new Date(Date.now() - RATE_LIMIT_MINUTES * 60_000).toISOString();
  const { data: recentTokens, error: rateErr } = await supabase
    .from("verification_tokens")
    .select("token")
    .eq("email", email)
    .eq("type", "password_reset")
    .is("used_at", null)
    .gte("created_at", fiveMinAgo)
    .limit(1);

  if (rateErr) {
    console.error("[forgot-password] rate limit query error", rateErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  if (recentTokens && recentTokens.length > 0) {
    return successResponse;
  }

  // 确认邮箱存在于 public.users 且已绑定 auth 账号
  const { data: userRow, error: userErr } = await supabase
    .from("users")
    .select("auth_id")
    .eq("email", email)
    .not("auth_id", "is", null)
    .maybeSingle();

  if (userErr) {
    console.error("[forgot-password] users query error", userErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  if (!userRow) {
    return successResponse;
  }

  // 作废同邮箱旧的未使用 password_reset token
  await supabase
    .from("verification_tokens")
    .update({ used_at: new Date().toISOString() })
    .eq("email", email)
    .eq("type", "password_reset")
    .is("used_at", null);

  // 生成新 token
  const expiresAt = new Date(Date.now() + 60 * 60 * 1000).toISOString();
  const { data: tokenRow, error: tokenErr } = await supabase
    .from("verification_tokens")
    .insert({
      email,
      type: "password_reset",
      waitlist_id: null,
      expires_at: expiresAt,
    })
    .select("token")
    .single();

  if (tokenErr || !tokenRow) {
    console.error("[forgot-password] token insert failed", tokenErr);
    return NextResponse.json({ error: "internal_error" }, { status: 500 });
  }

  // 发送重置邮件
  const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://star-coplay.luosea.cn").replace(/\/$/, "");
  const confirmationUrl = `${siteUrl}/reset-password?token=${tokenRow.token}`;
  const html = buildEmailHtml(confirmationUrl, siteUrl);

  try {
    const { Resend } = await import("resend");
    const resend = new Resend(process.env.RESEND_API_KEY);
    const senderEmail = process.env.RESEND_SENDER_EMAIL ?? "noreply@luosea.cn";

    const { error: sendErr } = await resend.emails.send({
      from: `StarCoPlay <${senderEmail}>`,
      to: email,
      subject: "【StarCoPlay】重置你的密码",
      html,
    });

    if (sendErr) {
      console.error("[forgot-password] resend send failed", sendErr);
      // 邮件发送失败不暴露给用户
    }
  } catch (e) {
    console.error("[forgot-password] resend threw", e instanceof Error ? e.message : e);
  }

  return successResponse;
}
