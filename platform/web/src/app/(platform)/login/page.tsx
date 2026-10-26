"use client";

/**
 * 登录页 — 邮箱+密码登录（Supabase Auth）
 *
 * 流程：
 *   1. 用户输入 email + password
 *   2. 调 supabase-browser client.auth.signInWithPassword
 *   3. 成功 → router.push('/me')；失败 → 展示具体错误
 *
 * 「忘记密码？」本期不实现，提示联系客服。
 * 「还没账号？去注册」→ /register
 */

import { useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";
import { validateEmailOptional } from "@/lib/register-validation";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [emailError, setEmailError] = useState("");
  const [passwordError, setPasswordError] = useState("");
  const [generalError, setGeneralError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [loading, setLoading] = useState(false);

  function validateForm(): boolean {
    setEmailError("");
    setPasswordError("");
    setGeneralError("");

    const emailTrimmed = email.trim();
    if (!emailTrimmed) {
      setEmailError("请输入邮箱");
      return false;
    }
    const emailValidation = validateEmailOptional(emailTrimmed);
    if (emailValidation) {
      setEmailError(emailValidation);
      return false;
    }
    if (!password) {
      setPasswordError("请输入密码");
      return false;
    }
    return true;
  }

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!validateForm()) return;

    setLoading(true);
    setGeneralError("");

    try {
      const supabase = createClient();
      const { error } = await supabase.auth.signInWithPassword({
        email: email.trim().toLowerCase(),
        password,
      });

      if (error) {
        const msg = (error.message || "").toLowerCase();
        if (msg.includes("invalid login credentials") || msg.includes("invalid_credentials")) {
          setGeneralError("邮箱或密码错误");
        } else if (msg.includes("email not confirmed")) {
          setGeneralError("邮箱尚未激活，请前往邮箱点击确认链接");
        } else if (msg.includes("network")) {
          setGeneralError("网络错误，请稍后重试");
        } else {
          setGeneralError(error.message || "登录失败");
        }
        return;
      }

      setSuccessMsg("登录成功，正在跳转…");
      // 短暂展示成功提示后跳转
      setTimeout(() => router.push("/me"), 300);
    } catch (err: unknown) {
      setGeneralError(err instanceof Error ? err.message : "网络错误，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] flex items-center justify-center px-4 py-8">
      <div className="parchment rounded-2xl p-8 max-w-sm w-full animate-fade-in-up">
        <div className="text-center mb-6">
          <Image
            src="/logo-new.png"
            alt="StarCoPlay"
            width={48}
            height={48}
            className="mx-auto mb-2"
          />
          <h1 className="font-[family-name:var(--font-pixel)] text-sm mb-1">
            登录 StarCoPlay
          </h1>
          <p className="text-xs text-stardew-brown/70">使用邮箱和密码登录</p>
        </div>

        <form onSubmit={handleLogin} className="space-y-4">
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
                if (emailError) setEmailError("");
                if (generalError) setGeneralError("");
              }}
              aria-invalid={Boolean(emailError)}
              required
              className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
                emailError
                  ? "border-red-400 focus:border-red-500"
                  : "border-stardew-wood/30 focus:border-stardew-wood"
              }`}
              placeholder="name@example.com"
              autoFocus
            />
            {emailError && (
              <p className="text-red-600 text-[10px] mt-1">{emailError}</p>
            )}
          </div>

          <div>
            <label className="block text-xs font-bold mb-1">
              密码 <span className="text-red-500">*</span>
            </label>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (passwordError) setPasswordError("");
                if (generalError) setGeneralError("");
              }}
              aria-invalid={Boolean(passwordError)}
              required
              className={`w-full px-3 py-2 rounded-lg border-2 bg-white/60 text-sm focus:outline-none ${
                passwordError
                  ? "border-red-400 focus:border-red-500"
                  : "border-stardew-wood/30 focus:border-stardew-wood"
              }`}
              placeholder="你的密码"
            />
            {passwordError && (
              <p className="text-red-600 text-[10px] mt-1">{passwordError}</p>
            )}
          </div>

          {generalError && (
            <p className="text-red-600 text-xs font-bold bg-red-50 p-2 rounded">
              {generalError}
            </p>
          )}
          {successMsg && (
            <p className="text-emerald-700 text-xs font-bold bg-emerald-50 p-2 rounded">
              {successMsg}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="stardew-btn w-full py-3 rounded-lg font-bold text-sm disabled:opacity-50"
          >
            {loading ? "登录中..." : "登录"}
          </button>
        </form>

        <div className="mt-6 flex flex-col gap-2 text-center">
          <p className="text-[10px] text-stardew-brown/60">
            还没账号？
            <a
              href="/register"
              className="text-stardew-blue hover:underline ml-1"
            >
              去注册
            </a>
          </p>
          <p className="text-[10px] text-stardew-brown/50">
            <a
              href="/forgot-password"
              className="text-stardew-blue/70 hover:underline"
            >
              忘记密码？
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
