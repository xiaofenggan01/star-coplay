"use client";

/**
 * 找回密码页（路径 /forgot-password）
 *
 * 流程：
 *   1. 用户输入邮箱
 *   2. POST /api/auth/forgot-password { email }
 *   3. 无论后端是否实际发了邮件，前端统一显示"请查收邮件"
 */

import { useState } from "react";
import Image from "next/image";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errMsg, setErrMsg] = useState("");
  const [sent, setSent] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setErrMsg("");

    const emailTrimmed = email.trim();
    if (!emailTrimmed) {
      setErrMsg("请输入邮箱");
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch("/api/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: emailTrimmed }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setErrMsg(data.error || "请求失败，请稍后重试");
        return;
      }

      setSent(true);
    } catch (err) {
      setErrMsg(err instanceof Error ? err.message : "网络错误，请稍后重试");
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

          {sent ? (
            <>
              <div className="text-3xl mb-3">📧</div>
              <h1 className="font-[family-name:var(--font-pixel)] text-sm mb-2">
                重置邮件已发送
              </h1>
              <p className="text-xs text-stardew-brown/80 leading-relaxed mb-6">
                如果邮箱已注册，你将在几分钟内收到重置密码的邮件。请检查收件箱（含垃圾邮件文件夹）。
              </p>
              <a
                href="/login"
                className="stardew-btn py-2.5 px-6 rounded-lg font-bold text-xs text-center no-underline inline-block"
              >
                返回登录
              </a>
            </>
          ) : (
            <>
              <h1 className="font-[family-name:var(--font-pixel)] text-sm mb-2">
                🔑 找回密码
              </h1>
              <p className="text-[10px] text-stardew-brown/55 mt-2 leading-relaxed">
                输入注册邮箱，我们将发送重置密码链接
              </p>
            </>
          )}
        </div>

        {!sent && (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-bold mb-1">
                邮箱 <span className="text-red-500">*</span>
              </label>
              <input
                type="email"
                inputMode="email"
                autoComplete="email"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  if (errMsg) setErrMsg("");
                }}
                required
                className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
                  errMsg
                    ? "border-red-400 focus:border-red-500"
                    : "border-stardew-wood/30 focus:border-stardew-wood"
                }`}
                placeholder="name@example.com"
                autoFocus
              />
            </div>

            {errMsg && (
              <p className="text-red-600 text-xs font-bold bg-red-50 p-2 rounded">
                {errMsg}
              </p>
            )}

            <button
              type="submit"
              disabled={submitting}
              className="stardew-btn w-full py-3 rounded-lg font-bold text-sm disabled:opacity-50"
            >
              {submitting ? "发送中…" : "发送重置邮件"}
            </button>

            <div className="text-center">
              <a
                href="/login"
                className="text-[10px] text-stardew-blue/70 hover:underline"
              >
                返回登录
              </a>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
