// 邀请码类型契约
// 对应 public.invite_codes 表（详见 migrations/20260512100000_invite_codes.sql）

export type InviteCodeType = "official" | "user";

export interface InviteCode {
  code: string;
  type: InviteCodeType;
  /** owner_user_id：user 类型必填；official 类型为 null */
  owner_user_id: string | null;
  max_uses: number | null;
  used_count: number;
  /** 仅 type='official' 有效，注册时复制到 profiles.official_invite_note */
  official_code_reason: string | null;
  created_at: string;
  expires_at: string | null;
  disabled: boolean;
}

/** consumeInviteCode 的失败原因 */
export type InviteCodeRejectReason =
  | "not_found"
  | "exhausted"   // used_count >= max_uses（official 码）
  | "invite_limit_reached" // used_count >= invite/policy.per_user_limit（user 码）
  | "disabled"
  | "expired";
