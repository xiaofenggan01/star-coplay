/**
 * /api/me/persona 业务核心（agentee 版本）：可单测的纯逻辑 + 默认依赖工厂。
 *
 * GET  → 读取 agentee_profiles + agentee_personas
 * PUT  → 校验 → upsert profile → 生成 guidelines → upsert persona
 * POST generate → 根据 tags/description 生成偏好预览（不入库）
 */

import { createServiceClient } from "./supabase-server";
import {
  parsePersonaDraft,
  compilePersonaArtifacts,
} from "./agentee-persona-generation";
import { BackendInternalError } from "./backend-internal";
import type {
  AgenteeProfile,
  AgenteePersona,
  AgenteePersonaInput,
  AgenteeConfigResponse,
  PersonaGenerateInput,
  PersonaGenerateOutput,
  CorePreferences,
  CustomFlavor,
  PersonaCompileOutput,
} from "./types/agentee";
import { validateAgenteeInput } from "./types/agentee";
import type { SupabaseClient } from "@supabase/supabase-js";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export type AgenteePersonaResult =
  | { status: number; body: AgenteeConfigResponse }
  | { status: number; body: PersonaGenerateOutput }
  | { status: number; body: { error: string; reason?: string } };

interface ProfileInput {
  display_name: string;
  user_call_name: string;
  relationship_type: string;
  short_bio?: string | null;
}

interface PersonaInput {
  custom_description?: string | null;
  core_preferences: CorePreferences;
  custom_flavor: CustomFlavor[];
}

export interface AgenteePersonaDeps {
  supabase: SupabaseClient;
  parseDraft: (input: PersonaGenerateInput) => Promise<PersonaGenerateOutput>;
  compilePersona: (input: {
    profile: ProfileInput;
    persona: PersonaInput;
  }) => Promise<PersonaCompileOutput>;
}

/* ------------------------------------------------------------------ */
/*  Core: GET                                                          */
/* ------------------------------------------------------------------ */

export async function handleGetAgenteePersona(
  userId: string,
  deps: AgenteePersonaDeps,
): Promise<AgenteePersonaResult> {
  try {
    const { data: profile, error: profileErr } = await deps.supabase
      .from("agentee_profiles")
      .select("*")
      .eq("user_id", userId)
      .eq("is_default", true)
      .maybeSingle();

    if (profileErr) throw new Error(`query profile failed: ${profileErr.message}`);

    if (!profile) {
      return { status: 200, body: { profile: null, persona: null } };
    }

    const { data: persona, error: personaErr } = await deps.supabase
      .from("agentee_personas")
      .select("*")
      .eq("agentee_id", profile.agentee_id)
      .maybeSingle();

    if (personaErr) throw new Error(`query persona failed: ${personaErr.message}`);

    return {
      status: 200,
      body: {
        profile: profile as AgenteeProfile,
        persona: (persona as AgenteePersona) ?? null,
      },
    };
  } catch (err) {
    if (err instanceof BackendInternalError) {
      return {
        status: err.status,
        body: { error: err.message, reason: "backend_internal_error" },
      };
    }
    const msg = err instanceof Error ? err.message : "internal error";
    return { status: 500, body: { error: msg, reason: "internal_error" } };
  }
}

/* ------------------------------------------------------------------ */
/*  Core: PUT                                                          */
/* ------------------------------------------------------------------ */

export async function handlePutAgenteePersona(
  userId: string,
  input: unknown,
  deps: AgenteePersonaDeps,
): Promise<AgenteePersonaResult> {
  // Validate
  const validated = validateAgenteeInput(input);
  if (!validated.ok) {
    return { status: 422, body: { error: validated.error, reason: "invalid_input" } };
  }
  const { profile: profileInput, persona: personaInput } = validated.data;

  try {
    const now = new Date().toISOString();
    const compiled = await deps.compilePersona({
      profile: {
        display_name: profileInput.display_name,
        user_call_name: profileInput.user_call_name,
        relationship_type: profileInput.relationship_type,
        short_bio: profileInput.short_bio,
      },
      persona: personaInput,
    });

    const profileData: Record<string, unknown> = {
      user_id: userId,
      is_default: true,
      display_name: profileInput.display_name,
      user_call_name: profileInput.user_call_name,
      relationship_type: profileInput.relationship_type,
      updated_at: now,
    };
    if (profileInput.avatar_url !== undefined) profileData.avatar_url = profileInput.avatar_url;
    if (profileInput.short_bio !== undefined) profileData.short_bio = profileInput.short_bio;

    const { data: existingProfile, error: existingProfileErr } = await deps.supabase
      .from("agentee_profiles")
      .select("*")
      .eq("user_id", userId)
      .eq("is_default", true)
      .maybeSingle();

    if (existingProfileErr) throw new Error(`query existing profile failed: ${existingProfileErr.message}`);

    const profileQuery = existingProfile
      ? deps.supabase
          .from("agentee_profiles")
          .update(profileData)
          .eq("agentee_id", existingProfile.agentee_id)
      : deps.supabase
          .from("agentee_profiles")
          .insert(profileData);

    const { data: profile, error: profileErr } = await profileQuery
      .select("*")
      .single();

    if (profileErr) throw new Error(`upsert profile failed: ${profileErr.message}`);

    // Upsert agentee_personas
    const { data: persona, error: personaErr } = await deps.supabase
      .from("agentee_personas")
      .upsert(
        {
          agentee_id: (profile as AgenteeProfile).agentee_id,
          user_id: userId,
          schema_version: 1,
          custom_description: personaInput.custom_description ?? null,
          core_preferences: personaInput.core_preferences,
          custom_flavor: personaInput.custom_flavor,
          style_summary: compiled.style_summary,
          speech_guidelines: compiled.speech_guidelines,
          collaboration_guidelines: compiled.collaboration_guidelines,
          gameplay_guidelines: compiled.gameplay_guidelines,
          persona_card: compiled.persona_card,
          generation_status: compiled.generation_status,
          generation_error: compiled.generation_error,
          generated_at: now,
          updated_at: now,
        },
        { onConflict: "agentee_id" },
      )
      .select("*")
      .single();

    if (personaErr) throw new Error(`upsert persona failed: ${personaErr.message}`);

    return {
      status: 200,
      body: {
        profile: profile as AgenteeProfile,
        persona: persona as AgenteePersona,
      },
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : "internal error";
    return { status: 500, body: { error: msg, reason: "internal_error" } };
  }
}

/* ------------------------------------------------------------------ */
/*  Core: POST generate (preview, no DB write)                         */
/* ------------------------------------------------------------------ */

export async function handleGeneratePreview(
  input: unknown,
  deps: AgenteePersonaDeps,
): Promise<AgenteePersonaResult> {
  // Validate input shape
  if (!input || typeof input !== "object") {
    return { status: 422, body: { error: "invalid request body", reason: "invalid_input" } };
  }
  const obj = input as Record<string, unknown>;

  const generateInput: PersonaGenerateInput = {};
  if (typeof obj.custom_description === "string" && obj.custom_description.trim()) {
    generateInput.custom_description = obj.custom_description.trim();
  }
  if (Array.isArray(obj.tags)) {
    generateInput.tags = obj.tags.filter((t): t is string => typeof t === "string");
  }

  if (!generateInput.custom_description && (!generateInput.tags || generateInput.tags.length === 0)) {
    return { status: 422, body: { error: "provide custom_description or tags", reason: "invalid_input" } };
  }

  try {
    const result = await deps.parseDraft(generateInput);
    return { status: 200, body: result };
  } catch (err) {
    if (err instanceof BackendInternalError) {
      return {
        status: err.status,
        body: { error: err.message, reason: "backend_internal_error" },
      };
    }
    const msg = err instanceof Error ? err.message : "llm_upstream_error";
    const statusMap: Record<string, number> = {
      llm_not_configured: 500,
      llm_timeout: 503,
      llm_upstream_error: 502,
      llm_invalid_response: 502,
    };
    const status = statusMap[msg] ?? 500;
    return { status, body: { error: msg, reason: "llm_error" } };
  }
}

/* ------------------------------------------------------------------ */
/*  Default deps factory                                               */
/* ------------------------------------------------------------------ */

export function buildDefaultAgenteePersonaDeps(): AgenteePersonaDeps {
  return {
    supabase: createServiceClient(),
    parseDraft: parsePersonaDraft,
    compilePersona: compilePersonaArtifacts,
  };
}
