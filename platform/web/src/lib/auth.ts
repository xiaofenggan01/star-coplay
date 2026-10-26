/**
 * 双轨鉴权统一入口 —— 阶段四核心 helper。
 *
 * 设计动机（详见 tech-spec §5.3）：
 *   Web 侧需要同时支持 Supabase JWT（新用户）和 platform_token（老用户）
 *   两套凭据。把分支逻辑统一到本模块，业务 route.ts 只关心
 *   AuthenticatedUser，不关心来源。
 *
 * 双轨流程（先 JWT 后 platform_token）：
 *   1. 提取 Authorization: Bearer <token>
 *   2. supabase.auth.getUser(token)
 *      - 成功 → public.users WHERE auth_id = $auth_id（找不到视为孤儿账号 → null）
 *      - 失败 → 进入 fallback
 *   3. public.users WHERE platform_token = $token
 *      - 成功 → source='platform_token'
 *      - 失败 → null
 *
 * 关键安全约束：
 *   - 必须使用 supabase.auth.getUser(token) 校验 JWT（验证 exp/aud/iss/签名）
 *     不能手解 base64 三段 JWT。
 *   - JWT 与 platform_token 不会互相误判：
 *     · JWT 形如 "xxx.yyy.zzz"（三段点号分隔的 base64url）
 *     · platform_token 是 UUID（无点号、定长）
 *     · supabase.auth.getUser(uuid) 会失败，自然 fallback
 *
 * DI 设计：
 *   AuthDeps 接口让单测可以注入 mock，避免在 unit test 中 boot Supabase。
 *   buildDefaultAuthDeps() 给 production / API Route 用。
 */

import { createServiceClient } from "./supabase-server";
import { createClient as createSupabaseClient } from "@supabase/supabase-js";
import type { AuthenticatedUser } from "./types/auth";

// ---------- DI 接口 ----------

export interface AuthDeps {
  /**
   * 校验 JWT。成功返回 auth.users.id 与 email；失败/非 JWT 返回 null。
   * 实现必须使用 supabase.auth.getUser(token)（含完整签名/过期校验）。
   */
  verifyJwt(token: string): Promise<{ auth_id: string; email: string | null } | null>;

  /**
   * 用 auth.users.id 查 public.users。
   * 找不到表示孤儿账号（auth.users 存在但 public.users 缺）—— 调用方应视为鉴权失败。
   */
  findUserByAuthId(
    auth_id: string,
  ): Promise<{ user_id: string; email: string | null } | null>;

  /**
   * 用 platform_token 查 public.users（老用户路径）。
   * 找不到返回 null。
   */
  findUserByPlatformToken(
    token: string,
  ): Promise<
    { user_id: string; auth_id: string | null; email: string | null } | null
  >;
}

// ---------- 默认依赖：基于真实 Supabase ----------

/**
 * verifyJwt 的默认实现。
 *
 * 说明：使用 anon key 创建一次性 client 调 getUser(token)。getUser 内部
 * 会调用 Supabase /auth/v1/user 端点，由服务端校验 JWT 签名 / 过期 / aud。
 * 不能用 service role 替代 anon —— service role 调用语义不同。
 */
function defaultVerifyJwt(): AuthDeps["verifyJwt"] {
  return async (token: string) => {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const anonKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
    if (!url || !anonKey) {
      // 配置缺失视为不可用，让 fallback 接管
      return null;
    }
    try {
      const client = createSupabaseClient(url, anonKey);
      const { data, error } = await client.auth.getUser(token);
      if (error || !data?.user) return null;
      return {
        auth_id: data.user.id,
        email: data.user.email ?? null,
      };
    } catch {
      return null;
    }
  };
}

function defaultFindUserByAuthId(): AuthDeps["findUserByAuthId"] {
  return async (auth_id: string) => {
    try {
      const supabase = createServiceClient();
      const { data, error } = await supabase
        .from("users")
        .select("user_id, email")
        .eq("auth_id", auth_id)
        .maybeSingle();
      if (error || !data) return null;
      return { user_id: data.user_id as string, email: (data.email as string | null) ?? null };
    } catch {
      return null;
    }
  };
}

function defaultFindUserByPlatformToken(): AuthDeps["findUserByPlatformToken"] {
  return async (token: string) => {
    try {
      const supabase = createServiceClient();
      const { data: ptData, error: ptError } = await supabase
        .from("platform_tokens")
        .select("user_id")
        .eq("token", token)
        .maybeSingle();
      if (ptError || !ptData) return null;
      const { data, error } = await supabase
        .from("users")
        .select("user_id, auth_id, email")
        .eq("user_id", ptData.user_id)
        .maybeSingle();
      if (error || !data) return null;
      return {
        user_id: data.user_id as string,
        auth_id: (data.auth_id as string | null) ?? null,
        email: (data.email as string | null) ?? null,
      };
    } catch {
      return null;
    }
  };
}

export function buildDefaultAuthDeps(): AuthDeps {
  return {
    verifyJwt: defaultVerifyJwt(),
    findUserByAuthId: defaultFindUserByAuthId(),
    findUserByPlatformToken: defaultFindUserByPlatformToken(),
  };
}

// ---------- 工具：从 Request 提取 Bearer token ----------

/**
 * 提取 Authorization: Bearer <token> 中的 token 部分。
 * - 缺 header / 缺 Bearer 前缀 / 空 token → null
 * - 大小写不敏感（Bearer / bearer 均接受）
 * - 自动 trim
 */
export function extractBearerToken(req: Request): string | null {
  const header = req.headers.get("authorization") ?? req.headers.get("Authorization");
  if (!header) return null;
  const m = /^Bearer\s+(.+)$/i.exec(header.trim());
  if (!m) return null;
  const token = m[1].trim();
  return token.length > 0 ? token : null;
}

// ---------- 核心 ----------

/**
 * 鉴权当前请求，返回统一的 AuthenticatedUser；失败返回 null。
 *
 * 调用方应在 null 时返回 401；找到用户后用 user.user_id 查业务数据。
 */
export async function authenticateRequest(
  req: Request,
  deps: AuthDeps = buildDefaultAuthDeps(),
): Promise<AuthenticatedUser | null> {
  const token = extractBearerToken(req);
  if (!token) return null;

  // Path 1: JWT
  const jwtResult = await deps.verifyJwt(token);
  if (jwtResult) {
    const userRow = await deps.findUserByAuthId(jwtResult.auth_id);
    if (!userRow) {
      // 孤儿账号：auth.users 有但 public.users 缺。视为鉴权失败。
      return null;
    }
    return {
      user_id: userRow.user_id,
      auth_id: jwtResult.auth_id,
      email: userRow.email ?? jwtResult.email,
      source: "jwt",
    };
  }

  // Path 2: platform_token fallback
  const platformRow = await deps.findUserByPlatformToken(token);
  if (!platformRow) return null;
  return {
    user_id: platformRow.user_id,
    auth_id: platformRow.auth_id,
    email: platformRow.email,
    source: "platform_token",
  };
}
