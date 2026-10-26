import type { SupabaseClient } from "@supabase/supabase-js";
import { readInvitePolicy, resolveInviteCodeUsageLimit } from "./invite-policy";

export interface AdminInviteUser {
  user_id: string;
  email: string | null;
  nickname: string | null;
  created_at: string | null;
}

export interface AdminUserInviteCode {
  code: string;
  used_count: number;
  max_uses: number | null;
  usage_limit: number | null;
  remaining_uses: number | null;
  disabled: boolean;
  invited_users: AdminInviteUser[];
}

export interface AdminUserInviteSummary {
  codes: AdminUserInviteCode[];
  invited_user_count: number;
  invited_users: AdminInviteUser[];
}

interface InviteCodeRow {
  code: string;
  owner_user_id: string;
  used_count: number | null;
  max_uses: number | null;
  disabled: boolean | null;
  created_at?: string | null;
}

interface RelationRow {
  user_id: string;
  inviter_user_id: string | null;
  invite_code: string | null;
  created_at: string | null;
}

interface UserRow {
  user_id: string;
  email: string | null;
  nickname: string | null;
  invite_code_used?: string | null;
  created_at: string | null;
}

interface ProfileRow {
  user_id: string;
  nickname: string | null;
}

function emptySummary(): AdminUserInviteSummary {
  return { codes: [], invited_user_count: 0, invited_users: [] };
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.map((value) => value?.trim()).filter((value): value is string => !!value))];
}

function mergeInvitedUser(
  map: Map<string, AdminInviteUser>,
  user: AdminInviteUser,
): void {
  const existing = map.get(user.user_id);
  if (!existing) {
    map.set(user.user_id, user);
    return;
  }
  map.set(user.user_id, {
    user_id: user.user_id,
    email: existing.email ?? user.email,
    nickname: existing.nickname ?? user.nickname,
    created_at: latestIso(existing.created_at, user.created_at),
  });
}

function latestIso(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return new Date(b).getTime() > new Date(a).getTime() ? b : a;
}

function sortedInviteUsers(map: Map<string, AdminInviteUser>): AdminInviteUser[] {
  return [...map.values()].sort((a, b) => {
    const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
    const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
    return bTime - aTime;
  });
}

export async function getAdminUserInviteSummaries(
  supabase: SupabaseClient,
  userIds: string[],
): Promise<Record<string, AdminUserInviteSummary>> {
  const ownerIds = uniqueStrings(userIds);
  const result: Record<string, AdminUserInviteSummary> = {};
  for (const userId of ownerIds) result[userId] = emptySummary();
  if (ownerIds.length === 0) return result;

  const { data: codeRows, error: codeErr } = await supabase
    .from("invite_codes")
    .select("code, owner_user_id, used_count, max_uses, disabled, created_at")
    .eq("type", "user")
    .in("owner_user_id", ownerIds)
    .order("created_at", { ascending: false });
  if (codeErr) throw new Error(`invite code lookup failed: ${codeErr.message}`);

  const codes = (codeRows ?? []) as InviteCodeRow[];
  const codeValues = uniqueStrings(codes.map((row) => row.code));
  if (codes.length === 0) return result;
  const invitePolicy = await readInvitePolicy(supabase);

  const codeUsers = new Map<string, Map<string, AdminInviteUser>>();
  for (const code of codeValues) codeUsers.set(code, new Map());

  const [relationsRes, directUsersRes] = await Promise.all([
    supabase
      .from("user_relations")
      .select("user_id, inviter_user_id, invite_code, created_at")
      .in("inviter_user_id", ownerIds)
      .in("invite_code", codeValues),
    supabase
      .from("users")
      .select("user_id, email, nickname, invite_code_used, created_at")
      .in("invite_code_used", codeValues),
  ]);
  if (relationsRes.error) throw new Error(`invite relation lookup failed: ${relationsRes.error.message}`);
  if (directUsersRes.error) throw new Error(`invited user lookup failed: ${directUsersRes.error.message}`);

  const relations = (relationsRes.data ?? []) as RelationRow[];
  const directUsers = (directUsersRes.data ?? []) as UserRow[];
  const invitedIds = uniqueStrings([
    ...relations.map((row) => row.user_id),
    ...directUsers.map((row) => row.user_id),
  ]);

  const userMap = new Map<string, UserRow>();
  const profileMap = new Map<string, ProfileRow>();
  if (invitedIds.length > 0) {
    const [usersRes, profilesRes] = await Promise.all([
      supabase
        .from("users")
        .select("user_id, email, nickname, created_at")
        .in("user_id", invitedIds),
      supabase
        .from("profiles")
        .select("user_id, nickname")
        .in("user_id", invitedIds),
    ]);
    if (usersRes.error) throw new Error(`invited user detail lookup failed: ${usersRes.error.message}`);
    if (profilesRes.error) throw new Error(`invited profile lookup failed: ${profilesRes.error.message}`);
    for (const row of (usersRes.data ?? []) as UserRow[]) userMap.set(row.user_id, row);
    for (const row of (profilesRes.data ?? []) as ProfileRow[]) profileMap.set(row.user_id, row);
  }

  function append(code: string | null | undefined, userId: string, createdAt: string | null): void {
    if (!code) return;
    const perCode = codeUsers.get(code);
    if (!perCode) return;
    const user = userMap.get(userId) ?? directUsers.find((row) => row.user_id === userId) ?? null;
    if (!user) return;
    const profile = profileMap.get(userId);
    mergeInvitedUser(perCode, {
      user_id: userId,
      email: user.email ?? null,
      nickname: profile?.nickname ?? user.nickname ?? null,
      created_at: createdAt ?? user.created_at ?? null,
    });
  }

  for (const row of directUsers) append(row.invite_code_used, row.user_id, row.created_at);
  for (const row of relations) append(row.invite_code, row.user_id, row.created_at);

  for (const row of codes) {
    const ownerSummary = result[row.owner_user_id] ?? emptySummary();
    const usedCount = Number(row.used_count ?? 0);
    const maxUses = row.max_uses == null ? null : Number(row.max_uses);
    const usageLimit = resolveInviteCodeUsageLimit("user", maxUses, invitePolicy);
    ownerSummary.codes.push({
      code: row.code,
      used_count: usedCount,
      max_uses: maxUses,
      usage_limit: usageLimit,
      remaining_uses: usageLimit == null ? null : Math.max(usageLimit - usedCount, 0),
      disabled: !!row.disabled,
      invited_users: sortedInviteUsers(codeUsers.get(row.code) ?? new Map()),
    });
    result[row.owner_user_id] = ownerSummary;
  }

  for (const summary of Object.values(result)) {
    const allUsers = new Map<string, AdminInviteUser>();
    for (const code of summary.codes) {
      for (const user of code.invited_users) mergeInvitedUser(allUsers, user);
    }
    summary.invited_users = sortedInviteUsers(allUsers);
    summary.invited_user_count = summary.invited_users.length;
  }

  return result;
}
