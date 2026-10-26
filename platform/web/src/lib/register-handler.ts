import { createServiceClient } from "./supabase-server";
import {
  consumeInviteCode,
  peekInviteCode,
  refundInviteCode,
  type ConsumeResult,
  type PeekInviteCodeResult,
} from "./invite-code";
import {
  openPlatformAccessByUserInvite,
  type OpenPlatformAccessByUserInviteResponse,
} from "./platform-access-invite-open";
import {
  openPlatformAccessByOfficialInvite,
  type OpenPlatformAccessByOfficialInviteResponse,
} from "./platform-access-official-open";
import { ensureProfileForUser } from "./profile-utils";
import { ensureUserRelation } from "./user-relations";
import type { InviteCode, InviteCodeRejectReason } from "./types/invite-code";
import type { OpenPlatformAccessResult } from "./platform-access-open";

export interface RegisterRequestBody {
  invite_code?: string;
  email?: string;
  password?: string;
  nickname?: string;
  phone?: string;
  wechat?: string;
  source?: string;
  waitlist_id?: string;
  apply_reason?: string;
}

export interface RegisterSuccess {
  user_id: string;
  email: string;
  resource_status: "guest" | "key_enabled";
  invite_code_type?: InviteCode["type"];
  official_invite_note?: string | null;
}

export type RegisterRejectReason =
  | InviteCodeRejectReason
  | "not_user_code"
  | "not_official_code"
  | "missing_owner"
  | "self_invite"
  | "relation_already_bound"
  | "open_access_failed"
  | "email_already_registered"
  | "auth_create_failed"
  | "user_creation_inconsistent"
  | "missing_field";

export interface RegisterError {
  error: string;
  reason?: RegisterRejectReason;
}

export type RegisterResult =
  | { status: number; body: RegisterSuccess }
  | { status: number; body: RegisterError };

export interface CreatedAuthUser {
  id: string;
  email: string | null;
}

export type CreateAuthUserResult =
  | { ok: true; user: CreatedAuthUser }
  | { ok: false; kind: "email_exists"; message: string }
  | { ok: false; kind: "other"; message: string };

export interface UserSnapshot {
  user_id: string;
  platform_token: string;
}

export interface EventInsertRow {
  user_id: string;
  type: string;
  ts: string;
  session_id: string;
  data: Record<string, unknown>;
  received_at: string;
}

export interface RegisterDeps {
  peekInviteCode: (code: string) => Promise<PeekInviteCodeResult>;
  consumeInviteCode: (code: string) => Promise<ConsumeResult>;
  refundInviteCode: (code: string) => Promise<void>;
  createAuthUser: (input: {
    email: string;
    password: string;
    metadata: Record<string, unknown>;
  }) => Promise<CreateAuthUserResult>;
  findUserByAuthId: (authId: string) => Promise<UserSnapshot | null>;
  updateProfileForRegistration: (
    userId: string,
    patch: { waitlist_id?: string; user_type: string; official_invite_note?: string | null },
  ) => Promise<void>;
  ensureUserRelation: (input: {
    user_id: string;
    inviter_user_id?: string | null;
    invite_code?: string | null;
  }) => Promise<void>;
  openPlatformAccessByUserInvite: (input: {
    user_id: string;
    email?: string | null;
    display_name?: string | null;
    waitlist_id?: string | number | null;
    invite_code: string;
    source?: string;
  }) => Promise<OpenPlatformAccessByUserInviteResponse>;
  openPlatformAccessByOfficialInvite: (input: {
    user_id: string;
    email?: string | null;
    display_name?: string | null;
    waitlist_id?: string | number | null;
    invite_code: string;
    source?: string;
  }) => Promise<OpenPlatformAccessByOfficialInviteResponse>;
  deleteRegistrationArtifacts: (input: {
    user_id: string;
    auth_id: string;
  }) => Promise<void>;
  recordFunnelEvent: (row: EventInsertRow) => Promise<void>;

  // Legacy deps kept optional so older tests/helpers can still construct RegisterDeps gradually.
  peekUserInviteCode?: unknown;
  isWaitlistApproved?: unknown;
  createWaitlistRecord?: unknown;
  readPlatformOnboardingPolicy?: unknown;
  openPlatformAccess?: (input: {
    user_id: string;
    email?: string | null;
    display_name?: string | null;
    waitlist_id?: string | number | null;
    source?: string;
  }) => Promise<OpenPlatformAccessResult>;
  markWaitlistRegistered?: unknown;
}

export async function handleRegister(
  deps: RegisterDeps,
  body: RegisterRequestBody,
): Promise<RegisterResult> {
  const code = normalizeInviteCode(body.invite_code ?? "");
  const email = (body.email ?? "").trim().toLowerCase();
  if (!email) {
    return { status: 400, body: { error: "email required", reason: "missing_field" } };
  }

  const password = body.password ?? "";
  if (!password) {
    return { status: 400, body: { error: "password required", reason: "missing_field" } };
  }

  let inviteRow: InviteCode | null = null;
  let userInviteRow: (InviteCode & { type: "user"; owner_user_id: string }) | null = null;
  if (code) {
    const peeked = await deps.peekInviteCode(code);
    if (!peeked.ok) {
      return {
        status: inviteRejectStatus(peeked.reason),
        body: { error: inviteRejectMessage(peeked.reason), reason: peeked.reason },
      };
    }
    inviteRow = peeked.code;
    if (inviteRow.type === "user") {
      const ownerUserId = inviteRow.owner_user_id?.trim();
      if (!ownerUserId) {
        return {
          status: inviteRejectStatus("missing_owner"),
          body: { error: inviteRejectMessage("missing_owner"), reason: "missing_owner" },
        };
      }
      userInviteRow = {
        ...inviteRow,
        type: "user",
        owner_user_id: ownerUserId,
      };
    }
  }

  const nickname = (body.nickname ?? "").trim();
  const createRes = await deps.createAuthUser({
    email,
    password,
    metadata: {
      invite_code: code || "",
      nickname,
      phone: body.phone?.trim() || null,
      wechat: body.wechat?.trim() || null,
      source: body.source || null,
      user_type: "guest",
    },
  });

  if (!createRes.ok) {
    if (createRes.kind === "email_exists") {
      return {
        status: 409,
        body: { error: "该邮箱已注册", reason: "email_already_registered" },
      };
    }
    return {
      status: 500,
      body: { error: createRes.message || "auth.createUser failed", reason: "auth_create_failed" },
    };
  }

  const authId = createRes.user.id;
  const snapshot = await deps.findUserByAuthId(authId);
  if (!snapshot) {
    console.error(
      `[register] CRITICAL inconsistent state: auth.users created but public.users missing, auth_id=${authId}, email=${email}`,
    );
    return {
      status: 500,
      body: {
        error: "用户记录创建异常，请联系运营",
        reason: "user_creation_inconsistent",
      },
    };
  }

  let officialInviteRow: (InviteCode & { type: "official" }) | null =
    inviteRow?.type === "official" ? { ...inviteRow, type: "official" } : null;

  let resourceStatus: RegisterSuccess["resource_status"] = "guest";
  let openFailureReason: RegisterRejectReason | null = null;
  let openFailureError: string | null = null;
  if (officialInviteRow) {
    try {
      const opened = await deps.openPlatformAccessByOfficialInvite({
        user_id: snapshot.user_id,
        email,
        display_name: nickname || email,
        invite_code: code,
        source: "register_with_official_invite_code",
      });
      if (opened.ok) {
        resourceStatus = "key_enabled";
        officialInviteRow = {
          ...officialInviteRow,
          official_code_reason: opened.result.official_invite_note,
        };
      } else {
        openFailureReason = opened.reason;
        openFailureError = opened.error ?? null;
      }
    } catch (error) {
      openFailureReason = "open_access_failed";
      openFailureError = error instanceof Error ? error.message : String(error);
    }

    if (openFailureReason) {
      await cleanupRegistrationArtifacts(deps, snapshot.user_id, authId);
      return {
        status: inviteRejectStatus(openFailureReason),
        body: {
          error: openFailureError || inviteRejectMessage(openFailureReason),
          reason: openFailureReason,
        },
      };
    }
  } else {
    await safeEnsureGuestRoot(deps, snapshot.user_id, null);
    await deps.updateProfileForRegistration(snapshot.user_id, { user_type: "guest" });
  }

  if (userInviteRow) {
    try {
      const opened = await deps.openPlatformAccessByUserInvite({
        user_id: snapshot.user_id,
        email,
        display_name: nickname || email,
        invite_code: code,
        source: "register_with_user_invite_code",
      });
      if (opened.ok) {
        resourceStatus = "key_enabled";
      } else {
        openFailureReason = opened.reason;
        openFailureError = opened.error ?? null;
      }
    } catch (error) {
      openFailureReason = "open_access_failed";
      openFailureError = error instanceof Error ? error.message : String(error);
    }

    if (openFailureReason) {
      await cleanupRegistrationArtifacts(deps, snapshot.user_id, authId);
      return {
        status: 503,
        body: {
          error: openFailureError || "open access failed",
          reason: "open_access_failed",
        },
      };
    }
  }

  await deps.recordFunnelEvent({
    user_id: snapshot.user_id,
    type: "funnel.registered",
    ts: new Date().toISOString(),
    session_id: "platform",
    data: {
      invite_code: code || null,
      invite_code_type: inviteRow?.type ?? null,
      official_invite_note: officialInviteRow?.official_code_reason ?? null,
      source: body.source || null,
      waitlist_id: null,
      auth_id: authId,
      resource_status: resourceStatus,
      open_failure_reason: openFailureReason,
      open_failure_error: openFailureError,
    },
    received_at: new Date().toISOString(),
  });

  return {
    status: 200,
    body: {
      user_id: snapshot.user_id,
      email,
      resource_status: resourceStatus,
      invite_code_type: inviteRow?.type,
      official_invite_note: officialInviteRow?.official_code_reason ?? null,
    },
  };
}

async function safeEnsureGuestRoot(
  deps: RegisterDeps,
  userId: string,
  inviteCode: string | null,
): Promise<void> {
  try {
    await deps.ensureUserRelation({
      user_id: userId,
      inviter_user_id: null,
      invite_code: inviteCode,
    });
  } catch (error) {
    console.error(
      `[register] guest root relation write failed user_id=${userId}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
}

async function cleanupRegistrationArtifacts(
  deps: RegisterDeps,
  userId: string,
  authId: string,
): Promise<void> {
  try {
    await deps.deleteRegistrationArtifacts({ user_id: userId, auth_id: authId });
  } catch (error) {
    console.error(
      `[register] cleanupRegistrationArtifacts failed user_id=${userId} auth_id=${authId}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
}

function normalizeInviteCode(raw: string): string {
  let value = raw.trim().normalize("NFKC");
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    value = value.slice(1, -1).trim();
  }
  return value.replace(/[‐‑‒–—―－]/g, "-");
}

function inviteRejectStatus(reason: RegisterRejectReason): number {
  if (reason === "invite_limit_reached" || reason === "exhausted") return 409;
  if (reason === "relation_already_bound" || reason === "self_invite") return 409;
  if (reason === "open_access_failed") return 503;
  return 403;
}

function inviteRejectMessage(reason: RegisterRejectReason): string {
  switch (reason) {
    case "not_found":
      return "邀请码不存在";
    case "disabled":
      return "邀请码已被禁用";
    case "expired":
      return "邀请码已过期";
    case "exhausted":
    case "invite_limit_reached":
      return "邀请码名额已用完";
    case "not_user_code":
    case "not_official_code":
    case "missing_owner":
      return "邀请码类型不支持当前操作";
    case "self_invite":
      return "不能使用自己的邀请码";
    case "relation_already_bound":
      return "当前账号已经绑定过邀请关系";
    case "open_access_failed":
      return "开通失败，请登录后在我的页面重新补充邀请码";
    default:
      return "注册被拒绝";
  }
}

export function buildDefaultDeps(): RegisterDeps {
  return {
    async peekInviteCode(code) {
      return peekInviteCode(code);
    },
    async consumeInviteCode(code) {
      return consumeInviteCode(code);
    },
    async refundInviteCode(code) {
      return refundInviteCode(code);
    },
    async createAuthUser({ email, password, metadata }) {
      const supabase = createServiceClient();
      const { data, error } = await supabase.auth.admin.createUser({
        email,
        password,
        email_confirm: true,
        user_metadata: metadata,
      });
      if (error) {
        const errCode = (error as { code?: string }).code;
        if (errCode === "email_exists" || errCode === "user_already_exists") {
          return { ok: false, kind: "email_exists", message: error.message };
        }
        return { ok: false, kind: "other", message: error.message };
      }
      const user = data?.user;
      if (!user) {
        return { ok: false, kind: "other", message: "createUser returned no user" };
      }
      return { ok: true, user: { id: user.id, email: user.email ?? null } };
    },
    async findUserByAuthId(authId) {
      const supabase = createServiceClient();
      const { data, error } = await supabase
        .from("users")
        .select("user_id, platform_token")
        .eq("auth_id", authId)
        .maybeSingle();
      if (error) {
        console.error(`[register] findUserByAuthId failed: ${error.message}`);
        return null;
      }
      if (!data) return null;
      return {
        user_id: data.user_id as string,
        platform_token: data.platform_token as string,
      };
    },
    async updateProfileForRegistration(userId, patch) {
      await ensureProfileForUser(userId, patch);
    },
    async ensureUserRelation(input) {
      await ensureUserRelation(input);
    },
    async openPlatformAccessByUserInvite(input) {
      return openPlatformAccessByUserInvite(input);
    },
    async openPlatformAccessByOfficialInvite(input) {
      return openPlatformAccessByOfficialInvite(input);
    },
    async deleteRegistrationArtifacts({ user_id, auth_id }) {
      const supabase = createServiceClient();
      const { error: relationErr } = await supabase
        .from("user_relations")
        .delete()
        .eq("user_id", user_id);
      if (relationErr) {
        throw new Error(`delete user_relations failed: ${relationErr.message}`);
      }
      const { error: relayErr } = await supabase
        .from("ai_relay_accounts")
        .delete()
        .eq("user_id", user_id);
      if (relayErr) {
        console.warn(`[register] delete ai_relay_accounts failed: ${relayErr.message}`);
      }
      const { error: profileErr } = await supabase
        .from("profiles")
        .delete()
        .eq("user_id", user_id);
      if (profileErr) {
        throw new Error(`delete profiles failed: ${profileErr.message}`);
      }
      const { error: userErr } = await supabase
        .from("users")
        .delete()
        .eq("user_id", user_id);
      if (userErr) {
        throw new Error(`delete users failed: ${userErr.message}`);
      }
      const { error: authErr } = await supabase.auth.admin.deleteUser(auth_id);
      if (authErr) {
        throw new Error(`delete auth user failed: ${authErr.message}`);
      }
    },
    async recordFunnelEvent(row) {
      try {
        const supabase = createServiceClient();
        const { error } = await supabase.from("events").insert(row);
        if (error) {
          console.warn(`[register] funnel event insert failed: ${error.message}`);
        }
      } catch (error) {
        console.warn(
          `[register] funnel event threw: ${
            error instanceof Error ? error.message : String(error)
          }`,
        );
      }
    },
  };
}
