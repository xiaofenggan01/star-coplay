import { createServiceClient } from "./supabase-server";
import type { SupabaseClient } from "@supabase/supabase-js";

export interface EnsureUserRelationInput {
  user_id: string;
  inviter_user_id?: string | null;
  invite_code?: string | null;
}

export type AttachInviteRelationResult =
  | { ok: true; status: "inserted" | "updated" | "already_bound" }
  | { ok: false; reason: "self_invite" | "relation_already_bound" };

export async function ensureUserRelation(
  input: EnsureUserRelationInput,
  supabase: SupabaseClient = createServiceClient(),
): Promise<void> {
  const userId = input.user_id.trim();
  if (!userId) return;

  const inviterId = input.inviter_user_id?.trim() || null;
  const inviteCode = input.invite_code?.trim() || null;
  let lineagePath = userId;
  let depth = 0;

  if (inviterId && inviterId !== userId) {
    const { data: inviterRelation } = await supabase
      .from("user_relations")
      .select("lineage_path, depth")
      .eq("user_id", inviterId)
      .maybeSingle();
    const inviterPath =
      typeof inviterRelation?.lineage_path === "string" && inviterRelation.lineage_path.trim()
        ? inviterRelation.lineage_path.trim()
        : inviterId;
    const inviterDepth =
      typeof inviterRelation?.depth === "number" && inviterRelation.depth >= 0
        ? inviterRelation.depth
        : 0;
    lineagePath = `${inviterPath}/${userId}`;
    depth = inviterDepth + 1;
  }

  const { error } = await supabase
    .from("user_relations")
    .upsert(
      {
        user_id: userId,
        inviter_user_id: inviterId && inviterId !== userId ? inviterId : null,
        invite_code: inviteCode,
        lineage_path: lineagePath,
        depth,
      },
      { onConflict: "user_id", ignoreDuplicates: true },
    );

  if (error) {
    throw new Error(`ensureUserRelation failed: ${error.message}`);
  }
}

export async function attachUserInviteRelation(
  input: Required<EnsureUserRelationInput>,
  supabase: SupabaseClient = createServiceClient(),
): Promise<AttachInviteRelationResult> {
  const userId = input.user_id.trim();
  const inviterId = input.inviter_user_id?.trim() || null;
  const inviteCode = input.invite_code?.trim() || null;
  if (!userId || !inviterId || !inviteCode || inviterId === userId) {
    return { ok: false, reason: "self_invite" };
  }

  const relation = await buildRelationPayload(
    { user_id: userId, inviter_user_id: inviterId, invite_code: inviteCode },
    supabase,
  );

  const { data: existing, error: existingErr } = await supabase
    .from("user_relations")
    .select("user_id, inviter_user_id, invite_code")
    .eq("user_id", userId)
    .maybeSingle();
  if (existingErr) {
    throw new Error(`attachUserInviteRelation lookup failed: ${existingErr.message}`);
  }

  if (!existing) {
    const { error } = await supabase.from("user_relations").insert(relation);
    if (error) {
      throw new Error(`attachUserInviteRelation insert failed: ${error.message}`);
    }
    return { ok: true, status: "inserted" };
  }

  const existingInviter =
    typeof existing.inviter_user_id === "string" && existing.inviter_user_id.trim()
      ? existing.inviter_user_id.trim()
      : null;
  const existingInviteCode =
    typeof existing.invite_code === "string" && existing.invite_code.trim()
      ? existing.invite_code.trim()
      : null;

  if (existingInviter === inviterId && existingInviteCode === inviteCode) {
    return { ok: true, status: "already_bound" };
  }
  if (existingInviter || existingInviteCode) {
    return { ok: false, reason: "relation_already_bound" };
  }

  const { data: updated, error: updateErr } = await supabase
    .from("user_relations")
    .update(relation)
    .eq("user_id", userId)
    .is("inviter_user_id", null)
    .is("invite_code", null)
    .select("user_id")
    .maybeSingle();
  if (updateErr) {
    throw new Error(`attachUserInviteRelation update failed: ${updateErr.message}`);
  }
  if (!updated) {
    return { ok: false, reason: "relation_already_bound" };
  }

  return { ok: true, status: "updated" };
}

export async function attachOfficialInviteRelation(
  input: { user_id: string; invite_code: string },
  supabase: SupabaseClient = createServiceClient(),
): Promise<AttachInviteRelationResult> {
  const userId = input.user_id.trim();
  const inviteCode = input.invite_code.trim();
  if (!userId || !inviteCode) {
    return { ok: false, reason: "relation_already_bound" };
  }

  const relation = await buildRelationPayload(
    { user_id: userId, inviter_user_id: null, invite_code: inviteCode },
    supabase,
  );

  const { data: existing, error: existingErr } = await supabase
    .from("user_relations")
    .select("user_id, inviter_user_id, invite_code")
    .eq("user_id", userId)
    .maybeSingle();
  if (existingErr) {
    throw new Error(`attachOfficialInviteRelation lookup failed: ${existingErr.message}`);
  }

  if (!existing) {
    const { error } = await supabase.from("user_relations").insert(relation);
    if (error) {
      throw new Error(`attachOfficialInviteRelation insert failed: ${error.message}`);
    }
    return { ok: true, status: "inserted" };
  }

  const existingInviter =
    typeof existing.inviter_user_id === "string" && existing.inviter_user_id.trim()
      ? existing.inviter_user_id.trim()
      : null;
  const existingInviteCode =
    typeof existing.invite_code === "string" && existing.invite_code.trim()
      ? existing.invite_code.trim()
      : null;

  if (!existingInviter && existingInviteCode === inviteCode) {
    return { ok: true, status: "already_bound" };
  }
  if (existingInviter || existingInviteCode) {
    return { ok: false, reason: "relation_already_bound" };
  }

  const { data: updated, error: updateErr } = await supabase
    .from("user_relations")
    .update(relation)
    .eq("user_id", userId)
    .is("inviter_user_id", null)
    .is("invite_code", null)
    .select("user_id")
    .maybeSingle();
  if (updateErr) {
    throw new Error(`attachOfficialInviteRelation update failed: ${updateErr.message}`);
  }
  if (!updated) {
    return { ok: false, reason: "relation_already_bound" };
  }

  return { ok: true, status: "updated" };
}

async function buildRelationPayload(
  input: EnsureUserRelationInput & { user_id: string },
  supabase: SupabaseClient,
): Promise<{
  user_id: string;
  inviter_user_id: string | null;
  invite_code: string | null;
  lineage_path: string;
  depth: number;
}> {
  const userId = input.user_id.trim();
  const inviterId = input.inviter_user_id?.trim() || null;
  const inviteCode = input.invite_code?.trim() || null;
  let lineagePath = userId;
  let depth = 0;

  if (inviterId && inviterId !== userId) {
    const { data: inviterRelation } = await supabase
      .from("user_relations")
      .select("lineage_path, depth")
      .eq("user_id", inviterId)
      .maybeSingle();
    const inviterPath =
      typeof inviterRelation?.lineage_path === "string" && inviterRelation.lineage_path.trim()
        ? inviterRelation.lineage_path.trim()
        : inviterId;
    const inviterDepth =
      typeof inviterRelation?.depth === "number" && inviterRelation.depth >= 0
        ? inviterRelation.depth
        : 0;
    lineagePath = `${inviterPath}/${userId}`;
    depth = inviterDepth + 1;
  }

  return {
    user_id: userId,
    inviter_user_id: inviterId && inviterId !== userId ? inviterId : null,
    invite_code: inviteCode,
    lineage_path: lineagePath,
    depth,
  };
}
