/**
 * profile-utils.ts — 共享的 profile 创建/更新工具函数
 *
 * 三条创建 auth.users 的路径（register / activate / link-email）都需要确保
 * public.profiles 记录存在。本模块提供统一的 upsert 函数，避免重复实现。
 *
 * 语义：ON CONFLICT (user_id) DO UPDATE — 不存在则创建，已存在则更新指定字段。
 */

import { createServiceClient } from "./supabase-server";

export interface ProfilePatch {
  user_type: string;
  waitlist_id?: string;
  official_invite_note?: string | null;
}

/**
 * 确保 profiles 记录存在并更新指定字段。
 *
 * 使用 upsert（ON CONFLICT user_id DO UPDATE）：
 *   - 不存在 → INSERT（user_id + patch 字段）
 *   - 已存在 → UPDATE patch 字段
 *
 * 最佳努力：失败仅 log，不抛异常（调用方不应因 profile 写入失败而中断主流程）。
 */
export async function ensureProfileForUser(
  userId: string,
  patch: ProfilePatch,
): Promise<void> {
  try {
    const supabase = createServiceClient();
    const updateData: Record<string, unknown> = { user_type: patch.user_type };
    if (patch.waitlist_id) {
      updateData.waitlist_id = Number(patch.waitlist_id);
    }
    if ("official_invite_note" in patch) {
      updateData.official_invite_note = patch.official_invite_note;
    }
    const { error } = await supabase
      .from("profiles")
      .upsert(
        { user_id: userId, ...updateData },
        { onConflict: "user_id" },
      );
    if (error) {
      console.warn(`[profile-utils] ensureProfileForUser failed: ${error.message}`);
    }
  } catch (e) {
    console.warn(
      `[profile-utils] ensureProfileForUser threw: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
}

/**
 * 仅在 profile 不存在时创建（ON CONFLICT DO NOTHING）。
 *
 * 适用于 link-email 等场景：老用户可能已有 profile，不应覆盖现有 user_type。
 */
export async function ensureProfileExistsForUser(
  userId: string,
  defaults: ProfilePatch,
): Promise<void> {
  try {
    const supabase = createServiceClient();
    const insertData: Record<string, unknown> = {
      user_id: userId,
      user_type: defaults.user_type,
    };
    if (defaults.waitlist_id) {
      insertData.waitlist_id = Number(defaults.waitlist_id);
    }
    // ignoreDuplicates = true → ON CONFLICT DO NOTHING
    const { error } = await supabase
      .from("profiles")
      .upsert(insertData, { onConflict: "user_id", ignoreDuplicates: true });
    if (error) {
      console.warn(`[profile-utils] ensureProfileExistsForUser failed: ${error.message}`);
    }
  } catch (e) {
    console.warn(
      `[profile-utils] ensureProfileExistsForUser threw: ${e instanceof Error ? e.message : String(e)}`,
    );
  }
}
