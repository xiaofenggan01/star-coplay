"use client";

/**
 * 注册页：邀请码可选。
 *
 * 无邀请码注册为游客；用户邀请码注册成功后直接尝试开通内测资源。
 */

import { useEffect, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { FN, fnRegisterHeaders } from "@/lib/supabase";
import { createClient as createSupabaseBrowser } from "@/lib/supabase-browser";
import { readInviteCodeFromSearch } from "@/lib/register-ref";
import { QQGroupInfo } from "@/components/QQGroupModal";
import {
  normalizeEmailForSubmit,
  validateEmailOptional,
} from "@/lib/register-validation";

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

export default function RegisterPage() {
  const router = useRouter();

  // Form fields
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [consent, setConsent] = useState(false);
  const [showConsent, setShowConsent] = useState(false);

  const [inviteCode, setInviteCode] = useState("");

  // Form state
  const [loading, setLoading] = useState(false);
  const [errMsg, setErrMsg] = useState("");
  const [errCode, setErrCode] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<{
    email?: string;
    password?: string;
    passwordConfirm?: string;
  }>({});

  // Success states
  //   - null       未成功，展示表单
  //   - "loggedIn" 注册并自动登录成功，可直接进入我的页面
  //   - "confirm"  注册成功但需邮箱确认后才能登录
  const [successKind, setSuccessKind] = useState<"loggedIn" | "confirm" | null>(
    null,
  );

  useEffect(() => {
    const refInviteCode = readInviteCodeFromSearch(window.location.search);
    if (!refInviteCode) return;

    setInviteCode((current) => current.trim() || refInviteCode);
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrMsg("");
    setErrCode(null);
    setFieldErrors({});

    // Validate email (always required)
    const emailErr = validateEmailRequired(email);
    const passwordErr = validatePassword(password);
    const passwordConfirmErr =
      password !== passwordConfirm ? "两次密码输入不一致" : null;

    if (emailErr || passwordErr || passwordConfirmErr) {
      setFieldErrors({
        ...(emailErr ? { email: emailErr } : {}),
        ...(passwordErr ? { password: passwordErr } : {}),
        ...(passwordConfirmErr ? { passwordConfirm: passwordConfirmErr } : {}),
      });
      return;
    }

    setLoading(true);

    try {
      const trimmedInviteCode = inviteCode.trim();
      const body: Record<string, string> = {
        email: normalizeEmailForSubmit(email),
        password,
      };
      if (trimmedInviteCode) {
        body.invite_code = trimmedInviteCode;
      }
      const res = await fetch(FN.register, {
        method: "POST",
        headers: fnRegisterHeaders(),
        body: JSON.stringify(body),
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const reason = (data && (data.reason || data.code)) as string | undefined;
        const message = (data && data.error) as string | undefined;
        if (res.status === 409 && reason === "email_already_registered") {
          setErrCode("email_already_registered");
          setErrMsg("邮箱已被注册");
        } else if (res.status === 403) {
          setErrMsg(message || "注册被拒绝");
        } else {
          setErrMsg(message || `注册失败（HTTP ${res.status}）`);
        }
        return;
      }

      // 注册成功，尝试自动登录
      const submittedEmail = data.email ?? normalizeEmailForSubmit(email);
      const supabase = createSupabaseBrowser();

      try {
        const { error: signInErr } = await supabase.auth.signInWithPassword({
          email: submittedEmail,
          password,
        });

        if (signInErr) {
          const msg = (signInErr.message || "").toLowerCase();
          if (msg.includes("email not confirmed")) {
            try {
              await supabase.auth.resend({
                type: "signup",
                email: submittedEmail,
                options: {
                  emailRedirectTo: `${window.location.origin}/auth/email-confirmed`,
                },
              });
            } catch {
              /* swallow */
            }
          }
          setSuccessKind("confirm");
          return;
        }
      } catch {
        setSuccessKind("confirm");
        return;
      }

      setSuccessKind("loggedIn");
    } catch (err: unknown) {
      setErrMsg(err instanceof Error ? err.message : "网络错误");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-6">
      <div className="parchment rounded-2xl p-8 max-w-md w-full animate-fade-in-up">
        {/* Header */}
        <div className="text-center mb-6">
          <Image
            src="/logo-new.png"
            alt="StarCoPlay"
            width={48}
            height={48}
            className="mx-auto mb-2"
          />
          <h1 className="font-[family-name:var(--font-pixel)] text-sm mb-1">
            StarCoPlay 注册
          </h1>
          <p className="text-xs text-stardew-brown/70">
            创建账号，开始你的 AI 搭子之旅
          </p>
        </div>

        {/* 注册成功卡：展示 QQ 内测群二维码 + 群号 */}
        {successKind && (
          <div className="text-center space-y-4 py-2">
            <div className="bg-green-50 border-2 border-green-200 rounded-xl p-4">
              <p className="text-sm font-bold text-green-800 mb-2">
                注册成功！
              </p>
              {successKind === "confirm" ? (
                <>
                  <p className="text-xs text-green-700 leading-relaxed">
                    请查收邮箱确认邮件，确认后即可登录。
                  </p>
                  <p className="text-[10px] text-green-600 mt-2">
                    如未收到邮件，请检查垃圾邮件文件夹。
                  </p>
                </>
              ) : (
                <p className="text-xs text-green-700 leading-relaxed">
                  欢迎加入 StarCoPlay，你已登录成功。
                </p>
              )}
            </div>

            {/* QQ 内测群 */}
            <div className="bg-white/60 rounded-xl p-4">
              <p className="text-xs font-bold text-stardew-brown mb-1">
                加入 QQ 内测群
              </p>
              <p className="text-[10px] text-stardew-brown/70 mb-3">
                扫码或搜索群号加入内测群，获取最新动态和技术支持
              </p>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/assets/qq-group-qr.png"
                alt="QQ 内测群二维码"
                className="w-44 mx-auto mb-3 rounded-lg"
              />
              <p className="text-[10px] text-stardew-brown/60 mb-2">QQ 群号</p>
              <QQGroupInfo />
            </div>

            {successKind === "loggedIn" ? (
              <button
                type="button"
                onClick={() => router.push("/me")}
                className="stardew-btn w-full py-3 rounded-lg font-bold text-sm"
              >
                进入我的页面
              </button>
            ) : (
              <a
                href="/login"
                className="inline-block text-xs text-stardew-blue hover:underline"
              >
                前往登录
              </a>
            )}
          </div>
        )}

        {/* Registration Form */}
        {!successKind && (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Email */}
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
                  if (fieldErrors.email)
                    setFieldErrors((p) => ({ ...p, email: undefined }));
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
                <p className="text-red-600 text-[10px] mt-1">
                  {fieldErrors.email}
                </p>
              )}
            </div>

            {/* Password fields */}
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
                    if (fieldErrors.password)
                      setFieldErrors((p) => ({ ...p, password: undefined }));
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
                  <p className="text-red-600 text-[10px] mt-1">
                    {fieldErrors.password}
                  </p>
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
                      setFieldErrors((p) => ({
                        ...p,
                        passwordConfirm: undefined,
                      }));
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
                  <p className="text-red-600 text-[10px] mt-1">
                    {fieldErrors.passwordConfirm}
                  </p>
                )}
              </div>
            </div>

            {/* Invite code */}
            <div>
              <label className="block text-xs font-bold mb-1">
                邀请码（选填）
              </label>
              <input
                value={inviteCode}
                onChange={(e) => setInviteCode(e.target.value)}
                className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-sm focus:border-stardew-wood focus:outline-none tracking-wider"
                placeholder="没有邀请码也可以注册为游客"
              />
            </div>

            {/* Consent */}
            <label className="flex items-start gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
                className="mt-0.5 w-4 h-4 rounded border-2 border-stardew-wood/30 accent-stardew-wood flex-shrink-0"
              />
              <span className="text-[10px] text-stardew-brown/70 leading-relaxed">
                注册即表示同意
                <button
                  type="button"
                  onClick={(event) => {
                    event.preventDefault();
                    setShowConsent(true);
                  }}
                  className="font-bold text-stardew-blue hover:underline"
                >
                  《数据收集与知情同意书》
                </button>
              </span>
            </label>

            {/* Error message */}
            {errMsg && (
              <div className="text-red-600 text-xs font-bold bg-red-50 p-2 rounded">
                {errMsg}
                {errCode === "email_already_registered" && (
                  <>
                    {"，"}
                    <a
                      href="/login"
                      className="text-stardew-blue hover:underline"
                    >
                      直接登录
                    </a>
                  </>
                )}
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading || !consent}
              className="stardew-btn w-full py-3 rounded-lg font-bold text-sm disabled:opacity-50"
            >
              {loading ? "注册中..." : "注册"}
            </button>

            {/* Footer links */}
            <div className="flex items-center justify-center text-[10px] mt-2">
              <a href="/login" className="text-stardew-blue hover:underline">
                已有账号？登录
              </a>
            </div>
          </form>
        )}
      </div>

      {showConsent && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="parchment max-h-[80vh] w-full max-w-lg overflow-y-auto rounded-2xl p-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-sm font-bold text-stardew-brown">
                数据收集与知情同意书
              </h2>
              <button
                type="button"
                onClick={() => setShowConsent(false)}
                className="rounded-lg border-2 border-stardew-wood/25 bg-stardew-cream px-3 py-1 text-[12px] font-bold text-stardew-brown"
              >
                关闭
              </button>
            </div>
            <div className="space-y-2 text-[11px] leading-relaxed text-stardew-brown/70">
              <p>
                欢迎加入《Star-CoPlay》内测。为了改进 AI 搭子的体验，我们会在测试期间收集必要的使用与交互数据。
              </p>
              <p className="font-bold text-stardew-brown">一、数据用途</p>
              <p>
                数据用于内测激活、安装转化、联机时长、指令类型、响应延迟、错误率、反馈归类和体验优化。
              </p>
              <p className="font-bold text-stardew-brown">二、存储与保护</p>
              <p>
                数据仅存储在受限服务中，由核心研发团队按需访问，不出售给第三方。
              </p>
              <p className="font-bold text-stardew-brown">三、用户权利</p>
              <p>
                你可以联系工作人员退出内测或申请删除相关数据。
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
