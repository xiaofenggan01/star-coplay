/**
 * /api/me/profile 业务核心：可单测的纯逻辑 + 默认依赖工厂。
 *
 * 抽到 lib/ 而非放在 app/api/me/profile/route.ts 内的原因：
 *   Next.js 16 的 route.ts 对非 HTTP-handler 命名的导出会报类型错误
 *   (OmitWithTag<...> 约束)。把 handle* / *Deps / build*Deps
 *   挪到独立模块，route.ts 仅保留 GET/PATCH 薄包装。
 *
 * GET 流程：
 *   1. 鉴权（authenticateRequest）→ 401 if 失败
 *   2. service role 查 profiles WHERE user_id = $user_id
 *   3. 找不到（profile 还未创建，比如老用户）→ 200 + profile=null
 *   4. 找到 → 200 + 完整 Profile
 *
 * PATCH 流程：
 *   1. 鉴权 → 401 if 失败
 *   2. 解析请求体 → 服务端白名单过滤：仅 nickname / avatar_url / preferences 三字段
 *      （防止用户改 referrer_user_id / official_invite_note / config 等敏感字段）
 *   3. UPSERT profiles WHERE user_id = $user_id（缺失则创建）
 *   4. 返回更新后的 Profile
 *
 * 错误码：401 unauthorized / 422 invalid_input / 500 internal_error
 */

import { createServiceClient } from "./supabase-server";
import { isEnabledAvatarPreset } from "./avatar-presets";
import type { Profile, ProfileUpdate } from "./types/profile";
import type { AuthenticatedUser } from "./types/auth";

// ---------- 类型 ----------

export type MeProfileResult =
  | { status: number; body: { profile: Profile | null } }
  | { status: number; body: { error: string; reason?: string } };

export interface MeProfileDeps {
  /** 查询 profile，找不到返回 null。出错抛异常。 */
  findProfile: (user_id: string) => Promise<Profile | null>;
  /** UPSERT profile（白名单字段已过滤），返回最终 Profile。出错抛异常。 */
  upsertProfile: (user_id: string, patch: ProfileUpdate) => Promise<Profile>;
  /** 同步 legacy users 表昵称，供管理后台、Token 校验等旧链路读取。 */
  syncUserNickname?: (user_id: string, nickname: string) => Promise<void>;
  validateAvatarUrl?: (avatar_url: string) => Promise<boolean>;
}

// ---------- 工具：白名单过滤 ----------

/**
 * 服务端白名单过滤：只允许 nickname / avatar_url / preferences 三字段。
 *
 * 安全意图：客户端可能尝试发送 `{ referrer_user_id, official_invite_note,
 *   config, user_id, created_at, ... }` 等字段，必须**显式**只取允许字段。
 *   不能用 `{ ...body }` 散开后再 delete，避免遗漏。
 *
 * 类型校验（基础）：
 *   - nickname: string（≤ 64 字符）
 *   - avatar_url: string（≤ 2048 字符）
 *   - preferences: 普通对象（非数组、非 null）
 * 不通过 → 整个请求 422。
 */
export function sanitizeProfilePatch(
  raw: unknown,
): { ok: true; patch: ProfileUpdate } | { ok: false; error: string } {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, error: "invalid request body" };
  }
  const src = raw as Record<string, unknown>;
  const patch: ProfileUpdate = {};

  if (src.nickname !== undefined) {
    if (typeof src.nickname !== "string") {
      return { ok: false, error: "nickname must be string" };
    }
    const v = src.nickname.trim();
    if (v.length === 0 || v.length > 64) {
      return { ok: false, error: "nickname length must be 1..64" };
    }
    patch.nickname = v;
  }

  if (src.avatar_url !== undefined) {
    if (typeof src.avatar_url !== "string") {
      return { ok: false, error: "avatar_url must be string" };
    }
    const v = src.avatar_url.trim();
    if (v.length > 2048) {
      return { ok: false, error: "avatar_url too long" };
    }
    patch.avatar_url = v;
  }

  if (src.preferences !== undefined) {
    if (
      !src.preferences ||
      typeof src.preferences !== "object" ||
      Array.isArray(src.preferences)
    ) {
      return { ok: false, error: "preferences must be a plain object" };
    }
    patch.preferences = src.preferences as Record<string, unknown>;
  }

  if (
    patch.nickname === undefined &&
    patch.avatar_url === undefined &&
    patch.preferences === undefined
  ) {
    return { ok: false, error: "no allowed fields to update" };
  }

  return { ok: true, patch };
}

// ---------- 核心：GET ----------

export async function handleMeProfileGet(
  deps: MeProfileDeps,
  user: AuthenticatedUser,
): Promise<MeProfileResult> {
  try {
    const profile = await deps.findProfile(user.user_id);
    return { status: 200, body: { profile } };
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return { status: 500, body: { error: msg, reason: "internal_error" } };
  }
}

// ---------- 核心：PATCH ----------

export async function handleMeProfilePatch(
  deps: MeProfileDeps,
  user: AuthenticatedUser,
  rawBody: unknown,
): Promise<MeProfileResult> {
  const sanitized = sanitizeProfilePatch(rawBody);
  if (!sanitized.ok) {
    return {
      status: 422,
      body: { error: sanitized.error, reason: "invalid_input" },
    };
  }

  try {
    if (sanitized.patch.avatar_url !== undefined) {
      const allowed = await deps.validateAvatarUrl?.(sanitized.patch.avatar_url);
      if (allowed === false) {
        return {
          status: 422,
          body: { error: "invalid avatar preset", reason: "invalid_avatar_preset" },
        };
      }
    }

    const profile = await deps.upsertProfile(user.user_id, sanitized.patch);
    if (sanitized.patch.nickname !== undefined) {
      await deps.syncUserNickname?.(user.user_id, sanitized.patch.nickname);
    }
    return { status: 200, body: { profile } };
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return { status: 500, body: { error: msg, reason: "internal_error" } };
  }
}

// ---------- 默认 deps：基于真实 supabase service client ----------

export function buildDefaultMeProfileDeps(): MeProfileDeps {
  return {
    async findProfile(user_id) {
      const supabase = createServiceClient();
      const { data, error } = await supabase
        .from("profiles")
        .select(
          "user_id, nickname, avatar_url, preferences, config, referrer_user_id, official_invite_note, user_type, created_at, updated_at",
        )
        .eq("user_id", user_id)
        .maybeSingle();
      if (error) {
        throw new Error(`findProfile failed: ${error.message}`);
      }
      if (!data) return null;
      return data as Profile;
    },

    async upsertProfile(user_id, patch) {
      const supabase = createServiceClient();
      // UPSERT：on conflict user_id → 更新允许字段；不存在则创建
      // 关键：不要把 referrer_user_id / official_invite_note / config 放进 row，
      // sanitize 已经把这些字段过滤掉了，这里再强调一次"白名单"的边界。
      const row: Record<string, unknown> = { user_id };
      if (patch.nickname !== undefined) row.nickname = patch.nickname;
      if (patch.avatar_url !== undefined) row.avatar_url = patch.avatar_url;
      if (patch.preferences !== undefined) row.preferences = patch.preferences;

      const { data, error } = await supabase
        .from("profiles")
        .upsert(row, { onConflict: "user_id" })
        .select(
          "user_id, nickname, avatar_url, preferences, config, referrer_user_id, official_invite_note, user_type, created_at, updated_at",
        )
        .single();
      if (error) {
        throw new Error(`upsertProfile failed: ${error.message}`);
      }
      return data as Profile;
    },

    async syncUserNickname(user_id, nickname) {
      const supabase = createServiceClient();
      const { error } = await supabase
        .from("users")
        .update({ nickname })
        .eq("user_id", user_id);
      if (error) {
        throw new Error(`syncUserNickname failed: ${error.message}`);
      }
    },
    async validateAvatarUrl(avatar_url) {
      return isEnabledAvatarPreset(avatar_url);
    },
  };
}
