/**
 * 内部鉴权辅助 —— 阶段四 A4a 引入。
 *
 * 设计动机（详见 tech-spec §5.3）：
 *   me/session/current、me/session/stream 等接口需要把请求透传给 WS HTTP API
 *   （PLATFORM_WS_HTTP_URL）。WS 服务只认 platform_token，不识别 Supabase JWT。
 *
 *   当 Web 端用户走 JWT 鉴权（user.source='jwt'）时，本接口必须用 user_id
 *   反查 public.users.platform_token，再用该 token 透传给 WS。
 *
 *   该查询走 service role（绕过 RLS），因为这是受信内部调用，已经过
 *   authenticateRequest 鉴权，可以安全地代表用户取 token。
 *
 * DI：
 *   PlatformTokenLookupDeps 接口让单测可注入 mock，避免真连 Supabase。
 *   buildDefaultPlatformTokenLookupDeps() 给 production / API Route 用。
 *
 * 安全约束：
 *   - 只能在 authenticateRequest 通过后调用（即调用方已确认 user_id 合法）
 *   - 不能把 platform_token 返回给客户端（仅在服务端用于内部透传）
 *   - 找不到 token 返回 null，调用方应返回 5xx 表示账号状态异常
 */

import { createServiceClient } from "./supabase-server";
import { assertPlatformKeyAccess } from "./platform-access";

// ---------- DI 接口 ----------

export interface PlatformTokenLookupDeps {
  assertPlatformKeyAccess?: (user_id: string) => Promise<void>;
  /**
   * 用 user_id 查 public.users.platform_token。
   * 找不到返回 null（账号被删 / 异常）。
   */
  findPlatformTokenByUserId(user_id: string): Promise<string | null>;
}

// ---------- 默认依赖：基于真实 Supabase ----------

function defaultFindPlatformTokenByUserId(): PlatformTokenLookupDeps["findPlatformTokenByUserId"] {
  return async (user_id: string) => {
    try {
      const supabase = createServiceClient();
      const { data, error } = await supabase
        .from("platform_tokens")
        .select("token")
        .eq("user_id", user_id)
        .order("created_at", { ascending: false })
        .limit(1)
        .maybeSingle();
      if (error || !data) return null;
      const token = (data.token as string | null) ?? null;
      return token && token.length > 0 ? token : null;
    } catch {
      return null;
    }
  };
}

export function buildDefaultPlatformTokenLookupDeps(): PlatformTokenLookupDeps {
  return {
    assertPlatformKeyAccess: async (user_id: string) => {
      await assertPlatformKeyAccess(user_id);
    },
    findPlatformTokenByUserId: defaultFindPlatformTokenByUserId(),
  };
}

// ---------- 核心 ----------

/**
 * 通过 user_id 取 platform_token，用于内部透传 WS HTTP API。
 *
 * 调用前提：
 *   - 调用方已通过 authenticateRequest 拿到 AuthenticatedUser
 *   - 仅在服务端使用，禁止把返回值发给客户端
 *
 * 失败语义：
 *   - 返回 null 表示该 user_id 无对应 platform_token（账号异常）
 *   - 调用方一般应返回 500 或 502
 */
export async function getPlatformTokenForUser(
  user_id: string,
  deps: PlatformTokenLookupDeps = buildDefaultPlatformTokenLookupDeps(),
): Promise<string | null> {
  if (!user_id) return null;
  await deps.assertPlatformKeyAccess?.(user_id);
  return deps.findPlatformTokenByUserId(user_id);
}
