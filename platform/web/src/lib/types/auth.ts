// 鉴权相关类型契约
// 阶段二：先定义 AuthSource / AuthenticatedUser 给 rate-limit / waitlist/check 用
// 阶段四：lib/auth.ts::authenticateRequest 返回 AuthenticatedUser

export type AuthSource = "jwt" | "platform_token";

export interface AuthenticatedUser {
  /** public.users.user_id（业务主键，所有业务表 FK 锚点） */
  user_id: string;
  /** 来自 Supabase Auth 的 auth.users.id，老用户可能为 null */
  auth_id: string | null;
  /** 鉴权来源，便于审计与监控 */
  source: AuthSource;
  /** 邮箱（可能为 null，老用户未补绑邮箱时） */
  email: string | null;
}

/** 鉴权失败时的错误码 */
export type AuthErrorCode =
  | "missing_token"
  | "invalid_token"
  | "user_not_found";
