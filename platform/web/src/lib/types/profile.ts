// 用户画像类型契约
// 对应 public.profiles 表（详见 migrations/20260512100200_profiles.sql）

export type UserType = "guest" | "beta_user" | "user" | "member";

export interface Profile {
  user_id: string;
  nickname: string | null;
  avatar_url: string | null;
  /** 用户偏好（语言/通知/主题等），用户可改 */
  preferences: Record<string, unknown>;
  /** 业务配置（agent 配置等），由后端写入 */
  config: Record<string, unknown>;
  /** 推荐人 user_id（来自邀请码 owner_user_id） */
  referrer_user_id: string | null;
  /** 官方邀请备注（type=official 时记录原因） */
  official_invite_note: string | null;
  created_at: string;
  user_type: UserType;
  updated_at: string;
}

/** 用户可通过 PATCH /api/me/profile 更新的字段子集 */
export interface ProfileUpdate {
  nickname?: string;
  avatar_url?: string;
  preferences?: Record<string, unknown>;
}

/** 服务端创建 profile 时的初始数据 */
export interface ProfileCreate {
  user_id: string;
  nickname?: string;
  referrer_user_id?: string;
  official_invite_note?: string;
}
