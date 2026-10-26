"use client";

/**
 * 平台导航栏（登录态感知）。
 *
 * 阶段四 A4e：
 *   - 'use client'，在挂载后异步获取 supabase.auth.getSession()
 *   - 已登录（有 Supabase Session）：右侧显示昵称/邮箱 + 「退出」按钮
 *   - 未登录：右侧显示 「登录」+「注册」入口
 *   - 监听 onAuthStateChange，登录/登出实时刷新
 *
 * 不阻塞首屏渲染：默认按"未登录"状态先静默渲染，挂载后异步更新；
 * 在 Session 状态确定前，右侧仅显示一个占位 placeholder，避免闪烁。
 *
 * 注意：本组件不感知 sessionStorage 里的 platform_token —— 老用户
 *       通过 platform_token 访问 /me 时，导航栏仍显示「登录/注册」，
 *       这是合理的：老用户尚未拥有 Supabase 账号，导航栏的"登录"
 *       指的就是补绑邮箱后的密码登录。
 */

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase-browser";

/** Routes that render the console sidebar — top nav shifts right + hides logo there */
const CONSOLE_PREFIXES = [
  "/me", "/persona", "/memory", "/skills", "/plan",
  "/channels", "/llm", "/appearance",
];

const linkCls =
  "font-[family-name:var(--font-sans)] text-sm font-medium leading-normal text-stardew-cream hover:text-stardew-gold transition-colors whitespace-nowrap";

const btnCls =
  "font-[family-name:var(--font-sans)] text-xs font-bold leading-normal text-stardew-brown bg-stardew-gold hover:bg-stardew-gold/80 transition-colors whitespace-nowrap rounded px-2 py-1";

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; label: string };

export default function PlatformNav() {
  const router = useRouter();
  const pathname = usePathname();
  const isConsole = CONSOLE_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(p + "/"),
  );
  const [auth, setAuth] = useState<AuthState>({ status: "loading" });

  useEffect(() => {
    let active = true;
    let supabase: ReturnType<typeof createClient> | null = null;
    try {
      supabase = createClient();
    } catch {
      // 缺失 env：保持未登录态
      setAuth({ status: "anonymous" });
      return () => {
        active = false;
      };
    }

    // 初次拉 session
    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (!active) return;
        const user = data.session?.user;
        if (user) {
          setAuth({
            status: "authenticated",
            label: deriveLabel(user.email ?? null, user.user_metadata),
          });
        } else {
          setAuth({ status: "anonymous" });
        }
      })
      .catch(() => {
        if (active) setAuth({ status: "anonymous" });
      });

    // 订阅状态变化
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!active) return;
      const user = session?.user;
      if (user) {
        setAuth({
          status: "authenticated",
          label: deriveLabel(user.email ?? null, user.user_metadata),
        });
      } else {
        setAuth({ status: "anonymous" });
      }
    });

    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, []);

  const handleSignOut = useCallback(async () => {
    try {
      const supabase = createClient();
      await supabase.auth.signOut();
    } catch {
      // ignore
    }
    // 同时清掉老用户 platform_token 存档，避免下次进入 me 时混淆
    try {
      sessionStorage.removeItem("platform_token_for_me");
    } catch {
      // ignore
    }
    setAuth({ status: "anonymous" });
    router.push("/");
    router.refresh();
  }, [router]);

  return (
    <header className="fixed top-0 inset-x-0 z-50 h-14 md:h-16 bg-stardew-brown/95 backdrop-blur-sm border-b-4 border-stardew-wood-dark">
      <div
        className={`h-full px-3 md:px-4 flex items-center gap-2 ${
          isConsole ? "md:pl-[15.5rem]" : "max-w-6xl mx-auto justify-between"
        }`}
      >
        {!isConsole && (
          <Link href="/" className="flex items-center shrink-0 -ml-1 md:-ml-2">
            <Image
              src="/assets/logo-new.png"
              alt="Star CoPlay"
              width={200}
              height={56}
              className="h-9 md:h-14 w-auto"
              priority
            />
          </Link>
        )}
        <nav
          className={`flex items-center gap-2 md:gap-5 overflow-x-auto py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden ${
            isConsole ? "flex-1 justify-between" : ""
          }`}
        >
          <div className="flex items-center gap-2 md:gap-5">
            <Link href="/" className={linkCls}>
              首页
            </Link>
            <Link href="/guide" className={linkCls}>
              安装指南
            </Link>
            <Link href="/me" className={linkCls}>
              我的数据
            </Link>
            <Link href="/dashboard" className={linkCls}>
              看板
            </Link>
            <Link href="/admin" className={linkCls}>
              管理
            </Link>
          </div>
          {/* 右侧账号区：根据 auth 状态切换 */}
          <span className="ml-1 md:ml-3 flex items-center gap-2 md:gap-3">
            {auth.status === "loading" ? (
              <span className="text-xs text-stardew-cream/50">…</span>
            ) : auth.status === "authenticated" ? (
              <>
                <span
                  className="text-xs text-stardew-cream/80 max-w-[140px] truncate"
                  title={auth.label}
                >
                  {auth.label}
                </span>
                <button type="button" onClick={handleSignOut} className={btnCls}>
                  退出
                </button>
              </>
            ) : (
              <>
                <Link href="/login" className={linkCls}>
                  登录
                </Link>
                <Link href="/register" className={linkCls}>
                  注册
                </Link>
              </>
            )}
          </span>
        </nav>
      </div>
    </header>
  );
}

/** 优先用 user_metadata.nickname，其次 email，再 fallback 占位 */
function deriveLabel(email: string | null, metadata: unknown): string {
  if (metadata && typeof metadata === "object") {
    const m = metadata as Record<string, unknown>;
    const nick = m.nickname;
    if (typeof nick === "string" && nick.trim().length > 0) {
      return nick.trim();
    }
  }
  if (email && email.length > 0) return email;
  return "已登录";
}
