/**
 * Supabase server clients —— 阶段四拆分版本
 *
 * 三个工厂的语义边界：
 *   - createServiceClient(): service role（绕过 RLS），用于注册、Admin 等
 *     特权操作。**禁止**在普通用户请求处理中使用。
 *   - createServerClient(): 用户上下文 client，绑定 Next.js cookies()
 *     用于 API Route 中读取/刷新当前用户的 Supabase Session。
 *   - 浏览器端 client 见 supabase-browser.ts
 *
 * createServerClient 的 cookies 适配：
 *   @supabase/ssr 要求宿主提供 getAll/setAll 两个方法以读写 sb-* cookies。
 *   Next.js 16 的 cookies() 返回 ReadonlyRequestCookies，写入仅在 Server
 *   Action / Route Handler 中合法；其他位置写入会抛错，因此在 setAll 中
 *   try/catch 吞掉，以兼容 Server Component 调用方。
 */
import { createClient } from "@supabase/supabase-js";
import {
  createServerClient as createSupabaseServerClient,
  type CookieOptions,
} from "@supabase/ssr";
import { cookies } from "next/headers";

export function createServiceClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY");
  }
  return createClient(url, key);
}

/**
 * 创建绑定到当前请求 cookies 的 Supabase client，用于 API Route /
 * Server Component 中读取当前用户 Session。
 *
 * 注意：在 Server Component 中调用，setAll 写入会被 try/catch 吞掉
 * （这是 Supabase 官方推荐写法，依赖 middleware.ts 完成 Session 刷新）。
 */
export async function createServerClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "Missing NEXT_PUBLIC_SUPABASE_URL or NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY",
    );
  }

  const cookieStore = await cookies();

  return createSupabaseServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet: { name: string; value: string; options: CookieOptions }[]) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Server Component 中不可写入；middleware.ts 会接管 Session 刷新
        }
      },
    },
  });
}
