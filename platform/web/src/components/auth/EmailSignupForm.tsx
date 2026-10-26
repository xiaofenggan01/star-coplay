"use client";

/**
 * EmailSignupForm — 注册表单组件
 *
 * 从 register/page.tsx 拆出的纯表单部分，复用注册 API（A4b 改造）。
 * 不包含：邀请码 modal、登录 modal、token 展示、知情同意书。
 *
 * Props:
 *   - initialInviteCode: 邀请码，可为空
 *   - initialWaitlistId: legacy waitlist 记录 ID（仅兼容旧入口）
 *   - onSuccess: 注册成功回调，参数 { user_id, email, resource_status }
 *
 * 提交：调 /api/register（A4b 新接口，body 含 email/password）。
 * 错误码映射：409 email_already_registered → "邮箱已被注册"；
 *            403 → 按后端 error 文本展示；其余按 error 文本展示。
 */

import { useState } from "react";
import { FN, fnRegisterHeaders } from "@/lib/supabase";
import { createClient as createSupabaseBrowser } from "@/lib/supabase-browser";
import {
  normalizeEmailForSubmit,
  normalizePhoneForSubmit,
  validateEmailOptional,
  validatePhoneOptional,
} from "@/lib/register-validation";

const SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "星露谷玩家", label: "我是星露谷玩家，想体验 AI 搭子" },
  { value: "AI 从业者", label: "我是 AI 从业者，对 Agent 技术感兴趣" },
  { value: "游戏行业", label: "我在游戏行业，关注 AI+游戏方向" },
  { value: "__other__", label: "其他（自填）" },
];

export interface EmailSignupSuccess {
  user_id: string;
  email: string;
  resource_status: "guest" | "key_enabled";
  /**
   * 注册成功后立即在组件内尝试 signInWithPassword 的结果。
   * 密码全程在闭包里，不写 sessionStorage / state（防 XSS 窃密）。
   *   - status='ok'      已登录，父组件可直接跳转
   *   - status='fail'    自动登录失败，父组件展示提示并引导手工登录
   *   - status='skipped' 浏览器异常或 supabase client 未配，父组件 fallback
   */
  autoLogin: {
    status: "ok" | "fail" | "skipped";
    error?: string;
  };
}

interface EmailSignupFormProps {
  initialInviteCode: string;
  initialWaitlistId?: string | null;
  /** 注册成功 + 自动登录已尝试后回调 */
  onSuccess?: (payload: EmailSignupSuccess) => void;
  /** 错误码 email_already_registered 时，触发跳转登录 */
  onEmailAlreadyRegistered?: (email: string) => void;
}

/** 密码强度：至少 8 字符 + 同时包含字母和数字 */
function validatePassword(raw: string): string | null {
  if (!raw) return "请输入密码";
  if (raw.length < 8) return "密码至少 8 位";
  if (!/[A-Za-z]/.test(raw)) return "密码需包含字母";
  if (!/\d/.test(raw)) return "密码需包含数字";
  return null;
}

function validateEmailRequired(raw: string): string | null {
  if (!raw.trim()) return "请输入邮箱";
  return validateEmailOptional(raw);
}

export default function EmailSignupForm({
  initialInviteCode,
  initialWaitlistId,
  onSuccess,
  onEmailAlreadyRegistered,
}: EmailSignupFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [nickname, setNickname] = useState("");
  const [phone, setPhone] = useState("");
  const [wechat, setWechat] = useState("");
  const [source, setSource] = useState("");
  const [sourceOther, setSourceOther] = useState("");
  const [loading, setLoading] = useState(false);
  const [errMsg, setErrMsg] = useState("");
  const [errCode, setErrCode] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{
    email?: string;
    password?: string;
    passwordConfirm?: string;
    phone?: string;
  }>({});

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrMsg("");
    setErrCode(null);
    setFieldErrors({});

    const emailErr = validateEmailRequired(email);
    const passwordErr = validatePassword(password);
    const passwordConfirmErr =
      password !== passwordConfirm ? "两次密码输入不一致" : null;
    const phoneErr = validatePhoneOptional(phone);

    if (emailErr || passwordErr || passwordConfirmErr || phoneErr) {
      setFieldErrors({
        ...(emailErr ? { email: emailErr } : {}),
        ...(passwordErr ? { password: passwordErr } : {}),
        ...(passwordConfirmErr ? { passwordConfirm: passwordConfirmErr } : {}),
        ...(phoneErr ? { phone: phoneErr } : {}),
      });
      return;
    }

    setLoading(true);

    try {
      let sourcePayload: string | undefined;
      if (source === "__other__") {
        const t = sourceOther.trim();
        sourcePayload = t ? `其他：${t}` : "其他";
      } else if (source) {
        sourcePayload = source;
      }

      const res = await fetch(FN.register, {
        method: "POST",
        headers: fnRegisterHeaders(),
        body: JSON.stringify({
          invite_code: initialInviteCode.trim(),
          email: normalizeEmailForSubmit(email),
          password,
          nickname: nickname.trim() || undefined,
          phone: normalizePhoneForSubmit(phone) || undefined,
          wechat: wechat.trim() || undefined,
          source: sourcePayload,
          waitlist_id: initialWaitlistId ?? undefined,
        }),
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const reason = (data && (data.reason || data.code)) as string | undefined;
        const message = (data && data.error) as string | undefined;
        if (res.status === 409 && reason === "email_already_registered") {
          setErrCode("email_already_registered");
          setErrMsg("邮箱已被注册");
          onEmailAlreadyRegistered?.(normalizeEmailForSubmit(email));
        } else if (res.status === 403) {
          setErrMsg(message || "注册被拒绝");
        } else {
          setErrMsg(message || `注册失败（HTTP ${res.status}）`);
        }
        return;
      }

      // 注册成功后立即尝试自动登录。密码仍在本函数闭包里，不写任何 storage。
      // signInWithPassword 完成后，闭包随函数退栈被 GC；密码只在 React state 与
      // supabase-js 内部 fetch body 中短暂存在（与任何登录页一致）。
      //
      // 关键：register API 用 service role admin.createUser 创建用户，**不会自动发激活邮件**
      //   （Supabase 行为：admin.createUser 视为可信操作，不触发 mailer）。
      // 因此当生产 enable_confirmations=true 时 signInWithPassword 必失败 'Email not confirmed'，
      // 必须客户端主动调 supabase.auth.resend({ type: 'signup' }) 让 GoTrue 走 SMTP 发激活邮件。
      const submittedEmail = data.email ?? normalizeEmailForSubmit(email);
      const supabase = createSupabaseBrowser();
      let autoLogin: EmailSignupSuccess["autoLogin"];
      try {
        const { error: signInErr } = await supabase.auth.signInWithPassword({
          email: submittedEmail,
          password,
        });
        if (signInErr) {
          const msg = (signInErr.message || "").toLowerCase();
          if (msg.includes("email not confirmed")) {
            // 主动触发激活邮件（admin.createUser 路径不会自动发邮件）
            // resend 失败不阻断；用户可在注册成功页用「重发激活邮件」按钮重试
            try {
              await supabase.auth.resend({
                type: "signup",
                email: submittedEmail,
                options: {
                  emailRedirectTo: `${window.location.origin}/auth/email-confirmed`,
                },
              });
            } catch {
              /* swallow; 用户仍可手动重发 */
            }
            autoLogin = {
              status: "fail",
              error: "激活邮件已发送，请前往邮箱点击确认链接后再登录",
            };
          } else {
            autoLogin = {
              status: "fail",
              error: signInErr.message || "自动登录失败，请前往登录页手动登录",
            };
          }
        } else {
          autoLogin = { status: "ok" };
        }
      } catch (signInThrown: unknown) {
        autoLogin = {
          status: "skipped",
          error:
            signInThrown instanceof Error
              ? signInThrown.message
              : "自动登录初始化失败",
        };
      }

      onSuccess?.({
        user_id: data.user_id,
        email: submittedEmail,
        resource_status: data.resource_status ?? "guest",
        autoLogin,
      });
    } catch (err: unknown) {
      setErrMsg(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-xs font-bold mb-1">
          邮箱 <span className="text-red-500">*</span>
        </label>
        <input
          type="email"
          inputMode="email"
          autoComplete="email"
          maxLength={254}
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
            if (fieldErrors.email) setFieldErrors((p) => ({ ...p, email: undefined }));
            if (errCode === "email_already_registered") {
              setErrCode(null);
              setErrMsg("");
            }
          }}
          aria-invalid={Boolean(fieldErrors.email)}
          required
          className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
            fieldErrors.email
              ? "border-red-400 focus:border-red-500"
              : "border-stardew-wood/30 focus:border-stardew-wood"
          }`}
          placeholder="name@example.com"
        />
        {fieldErrors.email && (
          <p className="text-red-600 text-[10px] mt-1">{fieldErrors.email}</p>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-bold mb-1">
            密码 <span className="text-red-500">*</span>
          </label>
          <input
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              if (fieldErrors.password) setFieldErrors((p) => ({ ...p, password: undefined }));
            }}
            aria-invalid={Boolean(fieldErrors.password)}
            required
            minLength={8}
            className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
              fieldErrors.password
                ? "border-red-400 focus:border-red-500"
                : "border-stardew-wood/30 focus:border-stardew-wood"
            }`}
            placeholder="≥ 8 位，含字母和数字"
          />
          {fieldErrors.password && (
            <p className="text-red-600 text-[10px] mt-1">{fieldErrors.password}</p>
          )}
        </div>
        <div>
          <label className="block text-xs font-bold mb-1">
            确认密码 <span className="text-red-500">*</span>
          </label>
          <input
            type="password"
            autoComplete="new-password"
            value={passwordConfirm}
            onChange={(e) => {
              setPasswordConfirm(e.target.value);
              if (fieldErrors.passwordConfirm)
                setFieldErrors((p) => ({ ...p, passwordConfirm: undefined }));
            }}
            aria-invalid={Boolean(fieldErrors.passwordConfirm)}
            required
            className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
              fieldErrors.passwordConfirm
                ? "border-red-400 focus:border-red-500"
                : "border-stardew-wood/30 focus:border-stardew-wood"
            }`}
            placeholder="再输入一次"
          />
          {fieldErrors.passwordConfirm && (
            <p className="text-red-600 text-[10px] mt-1">{fieldErrors.passwordConfirm}</p>
          )}
        </div>
      </div>

      <div>
        <label className="block text-xs font-bold mb-1">昵称</label>
        <input
          value={nickname}
          onChange={(e) => setNickname(e.target.value)}
          className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-sm focus:border-stardew-wood focus:outline-none"
          placeholder="你的游戏昵称（可选）"
        />
      </div>

      <p className="text-[10px] text-stardew-brown/60 leading-relaxed">
        建议尽量填写手机或微信，便于我们内测期间与您联系，跟进反馈。
      </p>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-bold mb-1">手机号</label>
          <input
            value={phone}
            onChange={(e) => {
              setPhone(e.target.value);
              if (fieldErrors.phone) setFieldErrors((p) => ({ ...p, phone: undefined }));
            }}
            inputMode="tel"
            autoComplete="tel"
            maxLength={24}
            aria-invalid={Boolean(fieldErrors.phone)}
            className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
              fieldErrors.phone
                ? "border-red-400 focus:border-red-500"
                : "border-stardew-wood/30 focus:border-stardew-wood"
            }`}
            placeholder="大陆 11 位或 +国际区号"
          />
          {fieldErrors.phone && (
            <p className="text-red-600 text-[10px] mt-1">{fieldErrors.phone}</p>
          )}
        </div>
        <div>
          <label className="block text-xs font-bold mb-1">微信号</label>
          <input
            value={wechat}
            onChange={(e) => setWechat(e.target.value)}
            className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-sm focus:border-stardew-wood focus:outline-none"
            placeholder="可选"
          />
        </div>
      </div>

      <div>
        <label className="block text-xs font-bold mb-1">你是怎么知道我们的？</label>
        <select
          value={source}
          onChange={(e) => {
            setSource(e.target.value);
            if (e.target.value !== "__other__") setSourceOther("");
          }}
          className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-sm focus:border-stardew-wood focus:outline-none"
        >
          <option value="">请选择（可选）</option>
          {SOURCE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        {source === "__other__" && (
          <input
            value={sourceOther}
            onChange={(e) => setSourceOther(e.target.value)}
            className="w-full mt-2 px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-sm focus:border-stardew-wood focus:outline-none"
            placeholder="请简要说明"
          />
        )}
      </div>

      {errMsg && (
        <div className="text-red-600 text-xs font-bold bg-red-50 p-2 rounded">
          {errMsg}
          {errCode === "email_already_registered" && (
            <>
              {"，"}
              <a href="/login" className="text-stardew-blue hover:underline">
                直接登录
              </a>
            </>
          )}
        </div>
      )}

      <button
        type="submit"
        disabled={loading}
        className="stardew-btn w-full py-3 rounded-lg font-bold text-sm disabled:opacity-50"
      >
        {loading ? "注册中..." : "注册"}
      </button>
    </form>
  );
}
