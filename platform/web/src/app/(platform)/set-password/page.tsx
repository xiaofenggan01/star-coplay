"use client";

/**
 * 设密码页（路径 /set-password）
 *
 * 新流程（Waitlist 邀请注册）：
 *   1. 用户收到邮件，链接是 /set-password?token=xxx
 *   2. GET /set-password 只展示表单，不消费 token
 *   3. 用户填密码提交 → POST /api/auth/activate → 成功后前端自己调 signInWithPassword 登录 → 跳 /me
 */

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import { createClient as createSupabaseBrowser } from "@/lib/supabase-browser";

function validatePassword(raw: string): string | null {
  if (!raw) return "请输入密码";
  if (raw.length < 8) return "密码至少 8 位";
  if (!/[A-Za-z]/.test(raw)) return "密码需包含字母";
  if (!/\d/.test(raw)) return "密码需包含数字";
  return null;
}

function SetPasswordFallback() {
  return (
    <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-8">
      <div className="parchment rounded-2xl p-8 max-w-md w-full text-center">
        <p className="text-xs text-stardew-brown/70">正在加载…</p>
      </div>
    </div>
  );
}

export default function SetPasswordPage() {
  return (
    <Suspense fallback={<SetPasswordFallback />}>
      <SetPasswordInner />
    </Suspense>
  );
}

function SetPasswordInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [okMsg, setOkMsg] = useState("");
  const [errMsg, setErrMsg] = useState("");
  const [fieldErrors, setFieldErrors] = useState<{
    password?: string;
    confirm?: string;
  }>({});

  // 无 token：展示友好错误
  if (!token) {
    return (
      <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-8">
        <div className="parchment rounded-2xl p-8 max-w-md w-full text-center animate-fade-in-up">
          <Image
            src="/logo-new.png"
            alt="StarCoPlay"
            width={48}
            height={48}
            className="mx-auto mb-4"
          />
          <div className="text-3xl mb-3">⚠️</div>
          <h1 className="font-[family-name:var(--font-pixel)] text-sm mb-2">
            链接无效
          </h1>
          <p className="text-xs text-stardew-brown/80 leading-relaxed mb-6">
            链接无效，请检查邮件或联系运营
          </p>
          <a
            href="/login"
            className="stardew-btn py-2.5 px-6 rounded-lg font-bold text-xs text-center no-underline inline-block"
          >
            前往登录
          </a>
        </div>
      </div>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setOkMsg("");
    setErrMsg("");
    setFieldErrors({});

    const pwdErr = validatePassword(password);
    const confirmErr = password !== confirm ? "两次密码输入不一致" : null;
    if (pwdErr || confirmErr) {
      setFieldErrors({
        ...(pwdErr ? { password: pwdErr } : {}),
        ...(confirmErr ? { confirm: confirmErr } : {}),
      });
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, password }),
      });
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        if (data.error === "token_already_used") {
          setErrMsg("此链接已使用，请直接登录");
        } else if (data.error === "token_expired") {
          setErrMsg("链接已过期（7天有效），请联系运营重新发送邀请");
        } else if (data.error === "email_already_registered") {
          setErrMsg("邮箱已注册，请直接登录");
        } else {
          setErrMsg(data.error || "激活失败，请稍后重试");
        }
        return;
      }

      // 成功：用返回的 email 自动登录
      setOkMsg("激活成功，正在登录…");
      const supabase = createSupabaseBrowser();
      await supabase.auth.signInWithPassword({ email: data.email, password });
      router.push("/me");
    } catch (err) {
      setErrMsg(err instanceof Error ? err.message : "网络错误");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-8">
      <div className="parchment rounded-2xl p-8 max-w-md w-full animate-fade-in-up">
        <div className="text-center mb-6">
          <Image
            src="/logo-new.png"
            alt="StarCoPlay"
            width={48}
            height={48}
            className="mx-auto mb-3"
          />
          <h1 className="font-[family-name:var(--font-pixel)] text-sm mb-2">
            🔑 设置登录密码
          </h1>
          <p className="text-[10px] text-stardew-brown/55 mt-2 leading-relaxed">
            设置后即可在登录页用邮箱+密码登录
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-bold mb-1">
              新密码 <span className="text-red-500">*</span>
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
              value={confirm}
              onChange={(e) => {
                setConfirm(e.target.value);
                if (fieldErrors.confirm)
                  setFieldErrors((p) => ({ ...p, confirm: undefined }));
              }}
              required
              className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
                fieldErrors.confirm
                  ? "border-red-400 focus:border-red-500"
                  : "border-stardew-wood/30 focus:border-stardew-wood"
              }`}
              placeholder="再输入一次"
            />
            {fieldErrors.confirm && (
              <p className="text-red-600 text-[10px] mt-1">{fieldErrors.confirm}</p>
            )}
          </div>

          {errMsg && (
            <p className="text-red-600 text-xs font-bold bg-red-50 p-2 rounded">
              {errMsg}
              {(errMsg.includes("已使用") || errMsg.includes("已注册")) && (
                <>
                  {"，"}
                  <a href="/login" className="text-stardew-blue hover:underline">
                    前往登录
                  </a>
                </>
              )}
            </p>
          )}
          {okMsg && (
            <p className="text-emerald-700 text-xs font-bold bg-emerald-50 p-2 rounded">
              {okMsg}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="stardew-btn w-full py-3 rounded-lg font-bold text-sm disabled:opacity-50"
          >
            {submitting ? "激活中…" : "设置密码并登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
