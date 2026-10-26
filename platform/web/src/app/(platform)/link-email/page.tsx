"use client";

/**
 * 老用户补绑邮箱页（阶段四 A4e）。
 *
 * 用途：
 *   老用户当前用 platform_token 登录（sessionStorage 存档）。
 *   通过本页提交 email + 密码，把 Supabase Auth 账号绑定到现有 user_id，
 *   之后即可使用邮箱密码登录，platform_token 仍然保留可用。
 *
 * 鉴权：
 *   仅接受 platform_token，前端先检查 sessionStorage.platform_token_for_me。
 *   缺失则提示用户先到 /me 输入 token，再返回本页。
 *
 * 错误码映射（来自 POST /api/me/link-email 后端契约）：
 *   - already_linked：当前用户已绑定邮箱
 *   - email_in_use  ：该邮箱已被其他账号使用
 *   - invalid_input ：邮箱/密码格式不合法
 *   - 其他          ：显示 server message
 *
 * 成功后：
 *   显示 toast "邮箱绑定成功，请用邮箱密码重新登录"
 *   清掉 sessionStorage.platform_token_for_me
 *   3 秒后跳 /login
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FN } from "@/lib/supabase";

type Status =
  | { kind: "idle" }
  | { kind: "submitting" }
  | { kind: "success" }
  | { kind: "error"; message: string };

const ERROR_MESSAGES: Record<string, string> = {
  already_linked: "你的账号已绑定邮箱，无需重复操作。",
  email_in_use: "该邮箱已被其他账号使用，请换一个或联系运营。",
  invalid_input: "邮箱或密码格式不合法（密码至少 8 位）。",
  unauthorized: "Platform Token 无效或已失效，请重新到「我的数据」输入。",
};

function emailValid(s: string): boolean {
  // 简单校验：含 @ 且至少 5 字符；具体合规校验由后端做
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s) && s.length <= 254;
}

function passwordValid(s: string): boolean {
  return s.length >= 8 && s.length <= 128;
}

export default function LinkEmailPage() {
  const router = useRouter();
  const [platformToken, setPlatformToken] = useState<string | null>(null);
  const [tokenChecked, setTokenChecked] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  // 启动时读 platform_token
  useEffect(() => {
    try {
      const t = sessionStorage.getItem("platform_token_for_me");
      setPlatformToken(t && t.trim().length > 0 ? t : null);
    } catch {
      setPlatformToken(null);
    } finally {
      setTokenChecked(true);
    }
  }, []);

  const formError = useMemo<string | null>(() => {
    if (!email && !password && !confirm) return null;
    if (email && !emailValid(email)) return "邮箱格式不正确";
    if (password && !passwordValid(password)) return "密码长度需在 8-128 位之间";
    if (confirm && confirm !== password) return "两次密码不一致";
    return null;
  }, [email, password, confirm]);

  const canSubmit =
    !!platformToken &&
    emailValid(email) &&
    passwordValid(password) &&
    confirm === password &&
    status.kind !== "submitting" &&
    status.kind !== "success";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || !platformToken) return;
    setStatus({ kind: "submitting" });
    try {
      const res = await fetch(FN.meLinkEmail, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${platformToken}`,
        },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        ok?: boolean;
        error?: string;
        reason?: string;
      };
      if (!res.ok || data.ok === false) {
        const code = data.reason ?? data.error ?? "unknown";
        const msg = ERROR_MESSAGES[code] ?? data.error ?? `提交失败（${res.status}）`;
        setStatus({ kind: "error", message: msg });
        return;
      }
      // 成功：清存档 + 倒计时跳登录
      try {
        sessionStorage.removeItem("platform_token_for_me");
      } catch {
        /* ignore */
      }
      setStatus({ kind: "success" });
      setTimeout(() => {
        router.push("/login");
      }, 3000);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "网络错误";
      setStatus({ kind: "error", message: msg });
    }
  }

  // 仍在检测 token：占位
  if (!tokenChecked) {
    return (
      <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] px-4 py-10">
        <div className="max-w-md mx-auto parchment rounded-2xl p-6 text-center">
          <p className="text-xs text-stardew-brown/60">正在检查 Platform Token…</p>
        </div>
      </div>
    );
  }

  // 没有 platform_token：阻断
  if (!platformToken) {
    return (
      <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] px-4 py-10">
        <div className="max-w-md mx-auto parchment rounded-2xl p-6">
          <h1 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-3">
            补绑邮箱
          </h1>
          <p className="text-xs text-stardew-brown/70 mb-4">
            未检测到 Platform Token。补绑邮箱前需要先用 token 登录「我的数据」。
          </p>
          <div className="flex gap-2">
            <Link
              href="/me"
              className="stardew-btn px-4 py-2 rounded-lg text-xs font-bold"
            >
              去「我的数据」
            </Link>
            <Link
              href="/login"
              className="text-stardew-blue hover:underline text-xs self-center"
            >
              已有账号？去登录
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="stardew-sky min-h-[calc(100dvh-3.5rem)] md:min-h-[calc(100dvh-4rem)] px-4 py-10">
      <div className="max-w-md mx-auto parchment rounded-2xl p-6">
        <h1 className="font-[family-name:var(--font-pixel)] text-sm text-stardew-brown mb-2">
          补绑邮箱
        </h1>
        <p className="text-xs text-stardew-brown/70 mb-5">
          为你现有账号绑定邮箱+密码。绑定后可用邮箱密码登录，原 Platform Token 仍可继续使用。
        </p>

        {status.kind === "success" ? (
          <div className="rounded-lg bg-stardew-green/15 border border-stardew-green/40 p-4 text-center">
            <p className="text-sm font-bold text-stardew-green mb-2">邮箱绑定成功 ✓</p>
            <p className="text-xs text-stardew-brown/70">
              请用邮箱密码重新登录，3 秒后自动跳转…
            </p>
            <p className="mt-3">
              <Link
                href="/login"
                className="text-stardew-blue hover:underline text-xs"
              >
                立即去登录 →
              </Link>
            </p>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-3">
            <div>
              <label htmlFor="email" className="block text-[11px] text-stardew-brown/70 mb-1">
                邮箱
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoComplete="email"
                placeholder="you@example.com"
                className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs focus:border-stardew-wood focus:outline-none"
              />
            </div>
            <div>
              <label htmlFor="password" className="block text-[11px] text-stardew-brown/70 mb-1">
                密码（至少 8 位）
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="new-password"
                minLength={8}
                maxLength={128}
                className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs font-mono focus:border-stardew-wood focus:outline-none"
              />
            </div>
            <div>
              <label htmlFor="confirm" className="block text-[11px] text-stardew-brown/70 mb-1">
                确认密码
              </label>
              <input
                id="confirm"
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                autoComplete="new-password"
                minLength={8}
                maxLength={128}
                className="w-full px-3 py-2 rounded-lg border-2 border-stardew-wood/30 bg-white/60 text-xs font-mono focus:border-stardew-wood focus:outline-none"
              />
            </div>

            {formError && (
              <p className="text-amber-700 text-[11px]">{formError}</p>
            )}
            {status.kind === "error" && (
              <p className="text-red-600 text-[11px]">{status.message}</p>
            )}

            <button
              type="submit"
              disabled={!canSubmit}
              className="stardew-btn w-full px-4 py-2 rounded-lg text-xs font-bold disabled:opacity-50"
            >
              {status.kind === "submitting" ? "提交中…" : "绑定邮箱"}
            </button>

            <p className="text-center pt-2">
              <Link href="/me" className="text-[10px] text-stardew-blue hover:underline">
                ← 返回我的数据
              </Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
