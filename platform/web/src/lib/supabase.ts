export const NEXT_PUBLIC_SUPABASE_URL =
  process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";
export const NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY =
  process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";

// Next.js API Routes（同站请求，无需完整 URL）
export const FN = {
  register: "/api/register",
  events: "/api/events",
  admin: "/api/admin",
  validateToken: "/api/validate-token",
  meSummary: "/api/me/summary",
  meSessionCurrent: "/api/me/session/current",
  meSessionStream: "/api/me/session/stream",
  // 阶段四新增（A4 业务层 subagent 将实现对应 route.ts）
  waitlistCheck: "/api/waitlist/check",
  meProfile: "/api/me/profile",
  meLinkEmail: "/api/me/link-email",
};

export function fnRegisterHeaders(): HeadersInit {
  return { "Content-Type": "application/json" };
}

export function fnAdminHeaders(adminSecret: string): HeadersInit {
  return { "x-admin-secret": adminSecret };
}

export function fnEventHeaders(platformToken: string): HeadersInit {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${platformToken}`,
  };
}
