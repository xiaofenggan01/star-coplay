/**
 * Next.js Middleware —— Supabase Session 自动刷新
 *
 * 职责：
 *   每个匹配请求触发一次 supabase.auth.getUser()，让 @supabase/ssr 检测
 *   sb-* cookie 中的 access_token 是否过期，过期则用 refresh_token 自动
 *   续签，并把新 cookies 写到响应中。
 *
 * 范围（本期）：
 *   只刷 Session，**不做强制登录守卫**。业务接口 / 页面自己用
 *   authenticateRequest / 客户端 supabase.auth 状态判断是否 401 / 重定向。
 *
 * matcher 排除：
 *   - _next/static / _next/image / favicon.ico：静态资源无需鉴权刷新
 *   - api/*：API Route 自己用 authenticateRequest 处理 Bearer 鉴权，
 *     无需中间件介入 cookie 刷新（Web 调用 API 时浏览器仍会自动带 cookie，
 *     但 API 通常用 Bearer，cookie 刷新对 SSR 页面才有意义）
 *
 * 参考：https://supabase.com/docs/guides/auth/server-side/nextjs
 */

import { NextResponse, type NextRequest } from "next/server";
import {
  createServerClient as createSupabaseServerClient,
  type CookieOptions,
} from "@supabase/ssr";

export async function middleware(req: NextRequest) {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

  // 配置缺失（如本地未配 env）：放行，不阻断请求；让业务层自己处理。
  if (!url || !anonKey) {
    return NextResponse.next({ request: req });
  }

  // 创建 response 时把 request headers 透传，便于下游 Server Component 拿到原 headers
  let response = NextResponse.next({
    request: req,
  });

  const supabase = createSupabaseServerClient(url, anonKey, {
    cookies: {
      getAll() {
        return req.cookies.getAll();
      },
      setAll(cookiesToSet: { name: string; value: string; options: CookieOptions }[]) {
        // 同步写入 request（让本次链路读到新值）+ response（让浏览器持久化）
        for (const { name, value } of cookiesToSet) {
          req.cookies.set(name, value);
        }
        response = NextResponse.next({ request: req });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  // 触发 Session 检查 / 刷新；忽略错误（无 session 是合法状态）
  try {
    await supabase.auth.getUser();
  } catch {
    // ignore
  }

  return response;
}

export const config = {
  matcher: [
    /**
     * 排除：
     * - _next/static / _next/image：构建产物
     * - favicon.ico / 各种静态文件后缀：无需 Session 刷新
     * - api/*：API Route 自己用 Bearer 鉴权，cookie 刷新对其无意义
     */
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|woff2?)$).*)",
  ],
};
