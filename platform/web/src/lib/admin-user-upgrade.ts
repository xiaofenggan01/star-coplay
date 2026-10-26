import { ensureAIRelayAccount, type EnsureAIRelayAccountResult } from "./ai-relay-sync";
import { grantInviteRewardsForUser, type InviteRewardRunResult } from "./invite-rewards";
import { ensureUserInviteCode } from "./platform-access-open";
import {
  attachOfficialInviteRelation,
  attachUserInviteRelation,
  ensureUserRelation,
} from "./user-relations";
import type { SupabaseClient } from "@supabase/supabase-js";

type UpgradeStage =
  | "parse"
  | "read_user"
  | "read_profile"
  | "validate_waitlist"
  | "read_invite"
  | "ensure_ai_relay"
  | "write_profile"
  | "write_relation"
  | "ensure_invite_code"
  | "grant_rewards"
  | "update_waitlist"
  | "audit";

interface AdminUpgradeUserRow {
  user_id: string;
  email: string | null;
  nickname: string | null;
  invite_code_used: string | null;
}

interface AdminUpgradeProfileRow {
  user_type: string | null;
  waitlist_id: number | string | null;
  nickname: string | null;
}

interface AdminUpgradeWaitlistRow {
  id?: number | string;
  status: string | null;
  email: string | null;
}

interface AdminUpgradeInviteRow {
  code: string;
  type: "official" | "user";
  owner_user_id: string | null;
  official_code_reason: string | null;
}

interface AdminUpgradeSuccessBody {
  ok: true;
  user_id: string;
  previous_user_type: string;
  user_type: "beta_user";
  repaired_existing_beta: boolean;
  reason: string;
  waitlist_id: number | null;
  waitlist_status_before: string | null;
  waitlist_status_after: string | null;
  invite_attribution: {
    invite_code: string | null;
    invite_type: "official" | "user" | null;
    inviter_user_id: string | null;
    relation_status: "inserted" | "updated" | "already_bound" | "root" | "skipped";
  };
  open_access: {
    ai_relay: EnsureAIRelayAccountResult;
    invite_code: string | null;
    invite_rewards: InviteRewardRunResult;
  };
}

interface AdminUpgradeErrorBody {
  ok?: false;
  error: string;
  reason?: string;
  stage?: UpgradeStage;
  user_type?: string;
  open_access?: Partial<AdminUpgradeSuccessBody["open_access"]>;
}

export type AdminUpgradeResult =
  | { status: number; body: AdminUpgradeSuccessBody }
  | { status: number; body: AdminUpgradeErrorBody };

export interface AdminUpgradeDeps {
  supabase: SupabaseClient;
  ensureAIRelayAccount?: typeof ensureAIRelayAccount;
  ensureUserInviteCode?: typeof ensureUserInviteCode;
  ensureUserRelation?: typeof ensureUserRelation;
  attachOfficialInviteRelation?: typeof attachOfficialInviteRelation;
  attachUserInviteRelation?: typeof attachUserInviteRelation;
  grantInviteRewardsForUser?: typeof grantInviteRewardsForUser;
  now?: () => Date;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === "object" && !Array.isArray(value);
}

function readString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readPositiveInt(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : Number.NaN;
  if (!Number.isInteger(parsed) || parsed <= 0) return null;
  return parsed;
}

function normalizeEmail(value: string | null | undefined): string | null {
  const trimmed = value?.trim().toLowerCase() ?? "";
  return trimmed || null;
}

function errorResult(status: number, error: string, reason: string, stage: UpgradeStage, extra?: Partial<AdminUpgradeErrorBody>): AdminUpgradeResult {
  return { status, body: { ok: false, error, reason, stage, ...extra } };
}

async function auditUpgrade(
  deps: AdminUpgradeDeps,
  input: {
    user_id: string;
    ok: boolean;
    stage: UpgradeStage;
    reason: string;
    previous_user_type?: string | null;
    waitlist_id?: number | null;
    error?: string | null;
    result?: Record<string, unknown>;
  },
): Promise<void> {
  const now = (deps.now?.() ?? new Date()).toISOString();
  try {
    await deps.supabase.from("events").insert({
      user_id: input.user_id,
      type: "admin.user_upgrade_to_beta",
      ts: now,
      session_id: "admin",
      received_at: now,
      data: {
        ok: input.ok,
        stage: input.stage,
        reason: input.reason,
        previous_user_type: input.previous_user_type ?? null,
        waitlist_id: input.waitlist_id ?? null,
        error: input.error ?? null,
        result: input.result ?? null,
      },
    });
  } catch (error) {
    console.warn(
      `[admin-user-upgrade] audit failed user_id=${input.user_id}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }
}

async function readInviteAttribution(
  supabase: SupabaseClient,
  inviteCode: string | null,
): Promise<AdminUpgradeInviteRow | null> {
  if (!inviteCode) return null;
  const { data, error } = await supabase
    .from("invite_codes")
    .select("code, type, owner_user_id, official_code_reason")
    .eq("code", inviteCode)
    .maybeSingle();
  if (error) {
    throw new Error(`invite lookup failed: ${error.message}`);
  }
  if (!data) return null;
  const row = data as AdminUpgradeInviteRow;
  if (row.type !== "official" && row.type !== "user") return null;
  return row;
}

async function resolveWaitlist(
  supabase: SupabaseClient,
  input: {
    requestedWaitlistId: number | null;
    profileWaitlistId: number | null;
    userEmail: string | null;
  },
): Promise<
  | { ok: true; waitlist_id: number | null; status_before: string | null }
  | { ok: false; status: number; error: string; reason: string }
> {
  const explicitWaitlistId = input.requestedWaitlistId != null;
  let waitlistId = input.requestedWaitlistId ?? input.profileWaitlistId;

  if (!waitlistId && input.userEmail) {
    const { data, error } = await supabase
      .from("waitlist")
      .select("id, status, email")
      .eq("email", input.userEmail)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (error) {
      throw new Error(`waitlist lookup failed: ${error.message}`);
    }
    const row = data as AdminUpgradeWaitlistRow | null;
    if (row?.id) {
      waitlistId = Number(row.id);
      return { ok: true, waitlist_id: waitlistId, status_before: row.status ?? null };
    }
  }

  if (!waitlistId) {
    return { ok: true, waitlist_id: null, status_before: null };
  }

  const { data, error } = await supabase
    .from("waitlist")
    .select("id, status, email")
    .eq("id", waitlistId)
    .maybeSingle();
  if (error) {
    throw new Error(`waitlist lookup failed: ${error.message}`);
  }
  if (!data) {
    return { ok: false, status: 422, error: "waitlist entry not found", reason: "waitlist_not_found" };
  }

  const row = data as AdminUpgradeWaitlistRow;
  const waitlistEmail = normalizeEmail(row.email);
  if (explicitWaitlistId && !waitlistEmail) {
    return {
      ok: false,
      status: 409,
      error: "explicit waitlist id cannot be verified against target user email",
      reason: "waitlist_unverifiable",
    };
  }
  if (waitlistEmail && waitlistEmail !== input.userEmail) {
    return {
      ok: false,
      status: 409,
      error: "waitlist email does not match target user",
      reason: "waitlist_email_mismatch",
    };
  }

  return { ok: true, waitlist_id: waitlistId, status_before: row.status ?? null };
}

async function writeBetaProfile(
  supabase: SupabaseClient,
  input: {
    user_id: string;
    profileExists: boolean;
    waitlist_id: number | null;
    official_invite_note: string | null;
  },
): Promise<
  | { ok: true; user_type: "beta_user" }
  | { ok: false; status: number; error: string; current_user_type: string | null }
> {
  const patch: Record<string, unknown> = { user_type: "beta_user" };
  if (input.waitlist_id) patch.waitlist_id = input.waitlist_id;
  if (input.official_invite_note !== null) patch.official_invite_note = input.official_invite_note;

  if (!input.profileExists) {
    const { error } = await supabase
      .from("profiles")
      .insert({ user_id: input.user_id, ...patch });
    if (!error) return { ok: true, user_type: "beta_user" };
    if (error.code !== "23505") {
      throw new Error(`profile insert failed: ${error.message}`);
    }
  }

  const { data, error } = await supabase
    .from("profiles")
    .update(patch)
    .eq("user_id", input.user_id)
    .in("user_type", ["guest", "beta_user"])
    .select("user_type")
    .maybeSingle();
  if (error) {
    throw new Error(`profile conditional update failed: ${error.message}`);
  }
  if (data) return { ok: true, user_type: "beta_user" };

  const { data: current, error: currentErr } = await supabase
    .from("profiles")
    .select("user_type")
    .eq("user_id", input.user_id)
    .maybeSingle();
  if (currentErr) {
    throw new Error(`profile recheck failed: ${currentErr.message}`);
  }
  const currentUserType = typeof current?.user_type === "string" ? current.user_type : null;
  return {
    ok: false,
    status: 409,
    error: "user type changed before upgrade could be written",
    current_user_type: currentUserType,
  };
}

async function ensureInviteRelation(
  deps: Required<Pick<AdminUpgradeDeps, "supabase">> & AdminUpgradeDeps,
  input: {
    user_id: string;
    invite: AdminUpgradeInviteRow | null;
  },
): Promise<AdminUpgradeSuccessBody["invite_attribution"]> {
  if (!input.invite) {
    await (deps.ensureUserRelation ?? ensureUserRelation)(
      { user_id: input.user_id, inviter_user_id: null, invite_code: null },
      deps.supabase,
    );
    return {
      invite_code: null,
      invite_type: null,
      inviter_user_id: null,
      relation_status: "root",
    };
  }

  if (input.invite.type === "official") {
    const relation = await (deps.attachOfficialInviteRelation ?? attachOfficialInviteRelation)(
      { user_id: input.user_id, invite_code: input.invite.code },
      deps.supabase,
    );
    if (!relation.ok) {
      throw new Error(`official invite relation failed: ${relation.reason}`);
    }
    return {
      invite_code: input.invite.code,
      invite_type: "official",
      inviter_user_id: null,
      relation_status: relation.status,
    };
  }

  const inviterUserId = input.invite.owner_user_id?.trim() || null;
  if (!inviterUserId || inviterUserId === input.user_id) {
    await (deps.ensureUserRelation ?? ensureUserRelation)(
      { user_id: input.user_id, inviter_user_id: null, invite_code: input.invite.code },
      deps.supabase,
    );
    return {
      invite_code: input.invite.code,
      invite_type: "user",
      inviter_user_id: inviterUserId,
      relation_status: "skipped",
    };
  }

  const relation = await (deps.attachUserInviteRelation ?? attachUserInviteRelation)(
    { user_id: input.user_id, inviter_user_id: inviterUserId, invite_code: input.invite.code },
    deps.supabase,
  );
  if (!relation.ok) {
    throw new Error(`user invite relation failed: ${relation.reason}`);
  }
  return {
    invite_code: input.invite.code,
    invite_type: "user",
    inviter_user_id: inviterUserId,
    relation_status: relation.status,
  };
}

export async function handleAdminUserUpgradeToBeta(
  deps: AdminUpgradeDeps,
  rawBody: unknown,
): Promise<AdminUpgradeResult> {
  let stage: UpgradeStage = "parse";
  let targetUserId = "";
  let reason = "";
  let previousUserType: string | null = null;
  let waitlistId: number | null = null;
  let openAccess: Partial<AdminUpgradeSuccessBody["open_access"]> = {};
  let userLoaded = false;

  async function fail(
    status: number,
    error: string,
    reasonCode: string,
    failedStage: UpgradeStage,
    extra?: Partial<AdminUpgradeErrorBody>,
  ): Promise<AdminUpgradeResult> {
    if (userLoaded && targetUserId) {
      await auditUpgrade(deps, {
        user_id: targetUserId,
        ok: false,
        stage: failedStage,
        reason: reason || reasonCode,
        previous_user_type: previousUserType,
        waitlist_id: waitlistId,
        error,
        result: openAccess as Record<string, unknown>,
      });
    }
    return errorResult(status, error, reasonCode, failedStage, extra);
  }

  try {
    if (!isRecord(rawBody)) {
      return errorResult(400, "invalid json", "invalid_json", stage);
    }

    targetUserId = readString(rawBody.user_id);
    reason = readString(rawBody.reason);
    if (!targetUserId) {
      return errorResult(422, "user_id required", "missing_user_id", stage);
    }
    if (!reason) {
      return errorResult(422, "upgrade reason required", "missing_reason", stage);
    }

    stage = "read_user";
    const { data: userData, error: userErr } = await deps.supabase
      .from("users")
      .select("user_id, email, nickname, invite_code_used")
      .eq("user_id", targetUserId)
      .maybeSingle();
    if (userErr) return errorResult(500, userErr.message, "user_lookup_failed", stage);
    if (!userData) return errorResult(404, "user not found", "user_not_found", stage);
    userLoaded = true;
    const user = userData as AdminUpgradeUserRow;
    const userEmail = normalizeEmail(user.email);

    stage = "read_profile";
    const { data: profileData, error: profileErr } = await deps.supabase
      .from("profiles")
      .select("user_type, waitlist_id, nickname")
      .eq("user_id", targetUserId)
      .maybeSingle();
    if (profileErr) return fail(500, profileErr.message, "profile_lookup_failed", stage);
    const profile = profileData as AdminUpgradeProfileRow | null;
    previousUserType = String(profile?.user_type ?? "guest");
    if (previousUserType !== "guest" && previousUserType !== "beta_user") {
      return fail(
        409,
        "only guest or existing beta users can be upgraded or repaired by this action",
        "unsupported_user_type",
        stage,
        { user_type: previousUserType },
      );
    }

    stage = "validate_waitlist";
    const waitlist = await resolveWaitlist(deps.supabase, {
      requestedWaitlistId: readPositiveInt(rawBody.waitlist_id),
      profileWaitlistId: readPositiveInt(profile?.waitlist_id),
      userEmail,
    });
    if (!waitlist.ok) {
      return fail(waitlist.status, waitlist.error, waitlist.reason, stage);
    }
    waitlistId = waitlist.waitlist_id;

    stage = "read_invite";
    const inviteCode = readString(user.invite_code_used) || null;
    const invite = await readInviteAttribution(deps.supabase, inviteCode);

    stage = "ensure_ai_relay";
    const aiRelay = await (deps.ensureAIRelayAccount ?? ensureAIRelayAccount)({
      user_id: targetUserId,
      email: user.email,
      display_name: profile?.nickname ?? user.nickname ?? user.email,
      source: "admin_user_upgrade_to_beta",
      timeout_ms: 15_000,
    });
    openAccess.ai_relay = aiRelay;
    if (!aiRelay.ok) {
      const status = aiRelay.status ? ` status=${aiRelay.status}` : "";
      const detail = aiRelay.error ? `: ${aiRelay.error}` : "";
      return fail(503, `AI Relay ensure failed${status}${detail}`, "ai_relay_failed", stage, {
        open_access: openAccess,
      });
    }

    stage = "write_relation";
    const relation = await ensureInviteRelation(deps, { user_id: targetUserId, invite });

    stage = "ensure_invite_code";
    const ownInviteCode = await (deps.ensureUserInviteCode ?? ensureUserInviteCode)(targetUserId, deps.supabase);
    openAccess.invite_code = ownInviteCode;

    stage = "write_profile";
    const profileWrite = await writeBetaProfile(deps.supabase, {
      user_id: targetUserId,
      profileExists: profile != null,
      waitlist_id: waitlistId,
      official_invite_note: invite?.type === "official" ? invite.official_code_reason : null,
    });
    if (!profileWrite.ok) {
      return fail(profileWrite.status, profileWrite.error, "profile_user_type_conflict", stage, {
        user_type: profileWrite.current_user_type ?? undefined,
        open_access: openAccess,
      });
    }

    stage = "grant_rewards";
    let inviteRewards: InviteRewardRunResult;
    try {
      inviteRewards = await (deps.grantInviteRewardsForUser ?? grantInviteRewardsForUser)(
        { user_id: targetUserId },
        deps.supabase,
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      console.warn(`[admin-user-upgrade] invite reward failed user_id=${targetUserId}: ${message}`);
      inviteRewards = {
        skipped: true,
        reason: "reward_error",
        error: message,
        grants: [],
      };
    }
    openAccess.invite_rewards = inviteRewards;

    let waitlistStatusAfter = waitlist.status_before;
    stage = "update_waitlist";
    if (waitlistId) {
      const { data: waitlistUpdated, error: waitlistUpdateErr } = await deps.supabase
        .from("waitlist")
        .update({ status: "registered" })
        .eq("id", waitlistId)
        .select("id, status")
        .maybeSingle();
      if (waitlistUpdateErr) {
        return fail(
          500,
          `open access succeeded but waitlist update failed: ${waitlistUpdateErr.message}`,
          "waitlist_update_failed",
          stage,
          { open_access: openAccess },
        );
      }
      if (!waitlistUpdated) {
        return fail(500, "open access succeeded but waitlist update matched no rows", "waitlist_update_missing", stage, {
          open_access: openAccess,
        });
      }
      waitlistStatusAfter = "registered";
    }

    const body: AdminUpgradeSuccessBody = {
      ok: true,
      user_id: targetUserId,
      previous_user_type: previousUserType,
      user_type: "beta_user",
      repaired_existing_beta: previousUserType === "beta_user",
      reason,
      waitlist_id: waitlistId,
      waitlist_status_before: waitlist.status_before,
      waitlist_status_after: waitlistStatusAfter,
      invite_attribution: relation,
      open_access: {
        ai_relay: aiRelay,
        invite_code: ownInviteCode,
        invite_rewards: inviteRewards,
      },
    };

    stage = "audit";
    await auditUpgrade(deps, {
      user_id: targetUserId,
      ok: true,
      stage,
      reason,
      previous_user_type: previousUserType,
      waitlist_id: waitlistId,
      result: {
        repaired_existing_beta: body.repaired_existing_beta,
        invite_attribution: body.invite_attribution,
        open_access: body.open_access,
      },
    });

    return { status: 200, body };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (targetUserId) {
      await auditUpgrade(deps, {
        user_id: targetUserId,
        ok: false,
        stage,
        reason,
        previous_user_type: previousUserType,
        waitlist_id: waitlistId,
        error: message,
        result: openAccess as Record<string, unknown>,
      });
    }
    return errorResult(500, message, "internal_error", stage, { open_access: openAccess });
  }
}
