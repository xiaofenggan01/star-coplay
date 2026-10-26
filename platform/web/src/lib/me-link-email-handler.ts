/**
 * /api/me/link-email 业务核心：可单测的纯逻辑 + 默认依赖工厂。
 *
 * 用途：老用户（用 platform_token 鉴权）补绑邮箱+密码，建立 auth.users 记录
 *      并把 public.users.auth_id 指向新 auth.users.id。
 *
 * 流程（核心：处理 trigger 双写）：
 *   1. 鉴权来源必须是 'platform_token'（JWT 用户已经有 auth_id，不应再绑）
 *   2. 校验请求体 email/password
 *   3. public.users.auth_id 必须为 null（已绑邮箱 → 422 already_linked）
 *   4. email 不能被其他 auth.users 占用（→ 409 email_in_use）
 *   5. service role 调 supabase.auth.admin.createUser({ email, password,
 *      user_metadata: { from_link_email: true, existing_user_id: oldUserId } })
 *   6. trigger 触发后会**多创建一条 public.users**（带新 user_id + 新 platform_token）
 *      → 后处理：
 *        a. UPDATE 老 public.users SET auth_id = newAuthId, email = email
 *           WHERE user_id = oldUserId
 *        b. DELETE FROM public.users WHERE auth_id = newAuthId AND user_id != oldUserId
 *           （删除 trigger 创建的"孤儿"记录）
 *
 * ⚠️ 原子性缺陷（必须正视）：
 *   - 跨 supabase API 无事务保护。若步骤 6.a 成功但 6.b 失败 → public.users
 *     存在两条带相同 auth_id 的记录（老 user_id + trigger 新建 user_id），后续
 *     按 auth_id 查 user 时会出现"找到多条"歧义；若 6.a 失败但 createUser 已
 *     成功 → auth.users 创建了新记录，public.users 多了 trigger 行，老 user 仍未
 *     绑定 auth_id，下次再 link 会被 already_linked 检测拦下（因为老 user 仍 null）
 *     但 email 检测会 409。
 *   - 缓解：本期通过严格的执行顺序 + 失败补偿（删除已创建 auth.users）降低概率
 *     未来优化：service role 直连 PG 用真事务，或新增 RPC 函数把 createUser
 *     之后的补偿放进数据库 SECURITY DEFINER 函数中。
 *   - DoD：trigger 双写问题已在代码注释 + 报告中明确文档化（见报告"link-email 原子性缺陷"）。
 *
 * 错误码：
 *   401 unauthorized
 *   422 invalid_input    （body 校验失败）
 *   422 already_linked   （source!=platform_token 或 auth_id 已存在）
 *   409 email_in_use     （email 已被 auth.users 占用）
 *   500 link_failed      （createUser/后处理失败）
 */

import { createServiceClient } from "./supabase-server";
import { ensureProfileExistsForUser } from "./profile-utils";
import { ensureAIRelayAccountBestEffort } from "./ai-relay-sync";
import type { AuthenticatedUser } from "./types/auth";

// ---------- 类型 ----------

export interface LinkEmailRequestBody {
  email?: string;
  password?: string;
}

export type LinkEmailResult =
  | { status: number; body: { ok: true; email: string } }
  | { status: number; body: { error: string; reason: string } };

export interface LinkEmailDeps {
  /** 检查 email 是否已被其他 auth.users 占用，true 表示已占用 */
  isEmailTaken: (email: string) => Promise<boolean>;
  /** 创建 auth.users，返回新 auth_id；失败抛异常 */
  createAuthUser: (
    email: string,
    password: string,
    existing_user_id: string,
  ) => Promise<string>;
  /** 把老 public.users 的 auth_id/email 更新为新值；失败抛异常 */
  linkOldUser: (
    old_user_id: string,
    new_auth_id: string,
    email: string,
  ) => Promise<void>;
  /** 删除 trigger 创建的孤儿 public.users（auth_id=新 auth_id 且 user_id != old_user_id） */
  deleteOrphanUsersByAuthId: (
    new_auth_id: string,
    keep_user_id: string,
  ) => Promise<void>;
  /** 失败补偿：删除已创建的 auth.users（避免 email 永久占用） */
  deleteAuthUser: (auth_id: string) => Promise<void>;
  ensureAIRelayAccount?: (input: {
    user_id: string;
    email?: string | null;
    display_name?: string | null;
    source?: string;
  }) => Promise<void>;
}

// ---------- 工具：基础校验 ----------

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function validateLinkEmailBody(
  raw: unknown,
): { ok: true; email: string; password: string } | { ok: false; error: string } {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return { ok: false, error: "invalid request body" };
  }
  const src = raw as Record<string, unknown>;
  const emailRaw = src.email;
  const passRaw = src.password;
  if (typeof emailRaw !== "string" || typeof passRaw !== "string") {
    return { ok: false, error: "email and password required" };
  }
  const email = emailRaw.trim().toLowerCase();
  if (!EMAIL_RE.test(email) || email.length > 254) {
    return { ok: false, error: "invalid email" };
  }
  if (passRaw.length < 8 || passRaw.length > 128) {
    return { ok: false, error: "password length must be 8..128" };
  }
  return { ok: true, email, password: passRaw };
}

// ---------- 核心 ----------

export async function handleMeLinkEmail(
  deps: LinkEmailDeps,
  user: AuthenticatedUser,
  rawBody: unknown,
): Promise<LinkEmailResult> {
  // ① 鉴权来源必须是 platform_token
  //    JWT 用户已经有 auth_id，不应该再走补绑流程
  if (user.source !== "platform_token") {
    return {
      status: 422,
      body: { error: "already linked via JWT", reason: "already_linked" },
    };
  }

  // ② 老 user 必须没绑过 auth_id（双重防护：authenticate 阶段也可能漏判）
  if (user.auth_id) {
    return {
      status: 422,
      body: { error: "user already has auth_id", reason: "already_linked" },
    };
  }

  // ③ 请求体校验
  const v = validateLinkEmailBody(rawBody);
  if (!v.ok) {
    return {
      status: 422,
      body: { error: v.error, reason: "invalid_input" },
    };
  }

  // ④ email 占用检查
  let taken = false;
  try {
    taken = await deps.isEmailTaken(v.email);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return { status: 500, body: { error: msg, reason: "link_failed" } };
  }
  if (taken) {
    return {
      status: 409,
      body: { error: "email already in use", reason: "email_in_use" },
    };
  }

  // ⑤ createUser → trigger 自动创建额外 public.users（孤儿）
  let newAuthId: string;
  try {
    newAuthId = await deps.createAuthUser(v.email, v.password, user.user_id);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return { status: 500, body: { error: msg, reason: "link_failed" } };
  }

  // ⑥ 后处理：把老 public.users 的 auth_id 设为 newAuthId
  //    若失败 → 补偿删除 auth.users 避免 email 永久占用
  try {
    await deps.linkOldUser(user.user_id, newAuthId, v.email);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    // 补偿：删除已创建的 auth.users（best effort，失败仅 log）
    try {
      await deps.deleteAuthUser(newAuthId);
    } catch (cleanupErr) {
      console.warn(
        `[link-email] cleanup deleteAuthUser failed: ${
          cleanupErr instanceof Error ? cleanupErr.message : String(cleanupErr)
        }`,
      );
    }
    return {
      status: 500,
      body: { error: `linkOldUser failed: ${msg}`, reason: "link_failed" },
    };
  }

  // ⑦ 删除 trigger 创建的孤儿 public.users（auth_id 与新 auth_id 相同的多余记录）
  //    ⚠️ 原子性缺陷：若此步失败，public.users 存在两条同 auth_id 记录
  //    缓解：失败仅 log + 报告告警；不回滚（老 user 已成功 link，不应破坏）
  try {
    await deps.deleteOrphanUsersByAuthId(newAuthId, user.user_id);
  } catch (err) {
    console.warn(
      `[link-email] deleteOrphanUsersByAuthId failed (auth_id=${newAuthId}, keep=${user.user_id}): ${
        err instanceof Error ? err.message : String(err)
      } — manual cleanup required`,
    );
    // 不返回错误：老 user 已成功补绑，孤儿记录是次要问题
    // 监控应通过"按 auth_id 反查 public.users 数 > 1"告警
  }

  // ⑧ 确保 profiles 记录存在（不覆盖已有值，仅兜底创建）
  await ensureProfileExistsForUser(user.user_id, { user_type: "guest" });

  try {
    await deps.ensureAIRelayAccount?.({
      user_id: user.user_id,
      email: v.email,
      display_name: v.email,
      source: "link_email",
    });
  } catch (err) {
    console.warn(
      `[link-email] ai relay ensure threw for user_id=${user.user_id}: ${
        err instanceof Error ? err.message : String(err)
      }`,
    );
  }

  return { status: 200, body: { ok: true, email: v.email } };
}

// ---------- 默认 deps：基于真实 supabase service client ----------

export function buildDefaultLinkEmailDeps(): LinkEmailDeps {
  return {
    async isEmailTaken(email) {
      const supabase = createServiceClient();
      // admin.listUsers 不直接支持按 email 过滤；用 getUserByEmail 不存在时
      // 回退到 listUsers 分页搜索代价高。这里改为先查 public.users（trigger 会写
      // email 到 public.users），覆盖 99% 场景；剩余兜底依赖 createUser 自身的
      // unique 约束（email 在 auth.users 上是 unique，冲突会抛 422）。
      const { data, error } = await supabase
        .from("users")
        .select("user_id")
        .eq("email", email)
        .not("auth_id", "is", null)
        .limit(1)
        .maybeSingle();
      if (error) {
        throw new Error(`isEmailTaken query failed: ${error.message}`);
      }
      return !!data;
    },

    async createAuthUser(email, password, existing_user_id) {
      const supabase = createServiceClient();
      const { data, error } = await supabase.auth.admin.createUser({
        email,
        password,
        email_confirm: true, // 老用户补绑直接置为已确认（已通过 platform_token 验证身份）
        user_metadata: {
          from_link_email: true,
          existing_user_id,
        },
      });
      if (error || !data?.user) {
        throw new Error(
          `auth.admin.createUser failed: ${error?.message ?? "no user returned"}`,
        );
      }
      return data.user.id;
    },

    async linkOldUser(old_user_id, new_auth_id, email) {
      const supabase = createServiceClient();
      const { error } = await supabase
        .from("users")
        .update({ auth_id: new_auth_id, email })
        .eq("user_id", old_user_id)
        // 防御：仅当 auth_id 仍为 null 时更新（防止并发重复绑定）
        .is("auth_id", null);
      if (error) {
        throw new Error(`linkOldUser update failed: ${error.message}`);
      }
    },

    async deleteOrphanUsersByAuthId(new_auth_id, keep_user_id) {
      const supabase = createServiceClient();
      const { error } = await supabase
        .from("users")
        .delete()
        .eq("auth_id", new_auth_id)
        .neq("user_id", keep_user_id);
      if (error) {
        throw new Error(
          `deleteOrphanUsersByAuthId delete failed: ${error.message}`,
        );
      }
    },

    async deleteAuthUser(auth_id) {
      const supabase = createServiceClient();
      const { error } = await supabase.auth.admin.deleteUser(auth_id);
      if (error) {
        throw new Error(`auth.admin.deleteUser failed: ${error.message}`);
      }
    },

    async ensureAIRelayAccount(input) {
      await ensureAIRelayAccountBestEffort(input);
    },
  };
}
