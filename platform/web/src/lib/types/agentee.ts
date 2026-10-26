/**
 * Agentee Persona 类型定义
 *
 * 对应 public.agentee_profiles + public.agentee_personas 表
 * 替代原 profiles.config.persona jsonb 方案，支持多维人格配置与 LLM 生成
 */

/* ------------------------------------------------------------------ */
/*  Core Data Structures                                               */
/* ------------------------------------------------------------------ */

/** 六维核心偏好（1-5 刻度） */
export interface CorePreferences {
  warmth: number;
  expressiveness: number;
  playfulness: number;
  initiative: number;
  carefulness: number;
  goal_focus: number;
}

/** 自定义风味特质 */
export interface CustomFlavor {
  label: string;
  description: string;
}

/** Agentee 身份信息（对应 agentee_profiles 表） */
export interface AgenteeProfile {
  agentee_id: string;
  user_id: string;
  is_default: boolean;
  display_name: string;
  avatar_url: string | null;
  user_call_name: string;
  relationship_type: string;
  short_bio: string | null;
  created_at: string;
  updated_at: string;
}

/** Agentee 人格配置与生成产物（对应 agentee_personas 表） */
export interface AgenteePersona {
  agentee_id: string;
  user_id: string;
  schema_version: number;
  custom_description: string | null;
  core_preferences: CorePreferences;
  custom_flavor: CustomFlavor[];
  style_summary: string | null;
  speech_guidelines: string[] | null;
  collaboration_guidelines: string[] | null;
  gameplay_guidelines: string[] | null;
  persona_card: string | null;
  generation_status: 'pending' | 'generated' | 'partial_fallback' | 'fallback';
  generation_error: string | null;
  generated_at: string | null;
  created_at: string;
  updated_at: string;
}

/* ------------------------------------------------------------------ */
/*  API Input/Output                                                    */
/* ------------------------------------------------------------------ */

/** 用户提交的配置（保存用） */
export interface AgenteePersonaInput {
  profile: {
    display_name: string;
    avatar_url?: string | null;
    user_call_name: string;
    relationship_type: string;
    short_bio?: string | null;
  };
  persona: {
    custom_description?: string | null;
    core_preferences: CorePreferences;
    custom_flavor: CustomFlavor[];
  };
}

/** GET /api/me/persona 响应 */
export interface AgenteeConfigResponse {
  profile: AgenteeProfile | null;
  persona: AgenteePersona | null;
}

/** POST /api/me/persona/generate 输入 */
export interface PersonaGenerateInput {
  custom_description?: string;
  tags?: string[];
}

/** POST /api/me/persona/generate 输出（预览，不直接入库） */
export interface PersonaGenerateOutput {
  core_preferences: CorePreferences;
  custom_flavor: CustomFlavor[];
  generated_by_model?: string;
  persona_schema_version?: number;
  generation_status?: "generated" | "partial_fallback" | "fallback";
  generation_error?: string | null;
}

/** POST /api/me/persona/compile 输入 */
export interface PersonaCompileInput {
  profile: AgenteePersonaInput["profile"];
  persona: AgenteePersonaInput["persona"];
}

/** POST /api/me/persona/compile 输出 */
export interface PersonaCompileOutput {
  style_summary: string;
  speech_guidelines: string[];
  collaboration_guidelines: string[];
  gameplay_guidelines: string[];
  persona_card: string;
  generated_by_model: string;
  persona_schema_version: number;
  generation_status: "generated" | "partial_fallback" | "fallback";
  generation_error: string | null;
}

/* ------------------------------------------------------------------ */
/*  Constants & Constraints                                            */
/* ------------------------------------------------------------------ */

export const AGENTEE_CONSTRAINTS = {
  DISPLAY_NAME_MAX: 20,
  USER_CALL_NAME_MAX: 10,
  RELATIONSHIP_TYPE_MAX: 20,
  SHORT_BIO_MAX: 100,
  CUSTOM_DESCRIPTION_MAX: 200,
  CUSTOM_FLAVOR_MAX: 3,
  CUSTOM_FLAVOR_LABEL_MAX: 12,
  CUSTOM_FLAVOR_DESC_MAX: 80,
  PREFERENCE_MIN: 1,
  PREFERENCE_MAX: 5,
} as const;

export const PREFERENCE_DIMENSIONS = [
  'warmth', 'expressiveness', 'playfulness',
  'initiative', 'carefulness', 'goal_focus',
] as const;
export type PreferenceDimension = typeof PREFERENCE_DIMENSIONS[number];

export const DEFAULT_CORE_PREFERENCES: CorePreferences = {
  warmth: 3,
  expressiveness: 3,
  playfulness: 3,
  initiative: 3,
  carefulness: 3,
  goal_focus: 3,
};

/* ------------------------------------------------------------------ */
/*  Tag Presets                                                         */
/* ------------------------------------------------------------------ */

export interface TagPreset {
  id: string;
  label: string;
  icon: string;
  preferences: CorePreferences;
}

export const TAG_PRESETS: TagPreset[] = [
  { id: 'gentle_reliable', label: '温柔可靠', icon: '🌸', preferences: { warmth: 5, expressiveness: 3, playfulness: 2, initiative: 4, carefulness: 4, goal_focus: 3 } },
  { id: 'tsundere', label: '嘴硬关心', icon: '💢', preferences: { warmth: 4, expressiveness: 2, playfulness: 4, initiative: 4, carefulness: 4, goal_focus: 4 } },
  { id: 'cheerful_active', label: '开朗活泼', icon: '☀️', preferences: { warmth: 4, expressiveness: 5, playfulness: 5, initiative: 5, carefulness: 2, goal_focus: 2 } },
  { id: 'calm_efficient', label: '沉稳高效', icon: '🎯', preferences: { warmth: 2, expressiveness: 2, playfulness: 1, initiative: 3, carefulness: 4, goal_focus: 5 } },
  { id: 'playful_teasing', label: '调皮损友', icon: '😈', preferences: { warmth: 3, expressiveness: 4, playfulness: 5, initiative: 5, carefulness: 1, goal_focus: 3 } },
  { id: 'quiet_companion', label: '安静陪伴', icon: '🌙', preferences: { warmth: 3, expressiveness: 1, playfulness: 1, initiative: 1, carefulness: 5, goal_focus: 3 } },
];

/* ------------------------------------------------------------------ */
/*  Validation                                                         */
/* ------------------------------------------------------------------ */

type ValidationResult =
  | { ok: true; data: AgenteePersonaInput }
  | { ok: false; error: string };

export function validateAgenteeInput(input: unknown): ValidationResult {
  if (!input || typeof input !== 'object') {
    return { ok: false, error: 'Input must be an object' };
  }

  const obj = input as Record<string, unknown>;

  // --- profile ---
  if (!obj.profile || typeof obj.profile !== 'object') {
    return { ok: false, error: 'Missing profile object' };
  }
  const profile = obj.profile as Record<string, unknown>;

  if (typeof profile.display_name !== 'string' || !profile.display_name.trim()) {
    return { ok: false, error: 'display_name is required' };
  }
  if (profile.display_name.length > AGENTEE_CONSTRAINTS.DISPLAY_NAME_MAX) {
    return { ok: false, error: `display_name exceeds ${AGENTEE_CONSTRAINTS.DISPLAY_NAME_MAX} chars` };
  }

  if (typeof profile.user_call_name !== 'string' || !profile.user_call_name.trim()) {
    return { ok: false, error: 'user_call_name is required' };
  }
  if (profile.user_call_name.length > AGENTEE_CONSTRAINTS.USER_CALL_NAME_MAX) {
    return { ok: false, error: `user_call_name exceeds ${AGENTEE_CONSTRAINTS.USER_CALL_NAME_MAX} chars` };
  }

  if (typeof profile.relationship_type !== 'string' || !profile.relationship_type.trim()) {
    return { ok: false, error: 'relationship_type is required' };
  }
  if (profile.relationship_type.length > AGENTEE_CONSTRAINTS.RELATIONSHIP_TYPE_MAX) {
    return { ok: false, error: `relationship_type exceeds ${AGENTEE_CONSTRAINTS.RELATIONSHIP_TYPE_MAX} chars` };
  }

  if (profile.short_bio != null && typeof profile.short_bio === 'string') {
    if (profile.short_bio.length > AGENTEE_CONSTRAINTS.SHORT_BIO_MAX) {
      return { ok: false, error: `short_bio exceeds ${AGENTEE_CONSTRAINTS.SHORT_BIO_MAX} chars` };
    }
  }

  // --- persona ---
  if (!obj.persona || typeof obj.persona !== 'object') {
    return { ok: false, error: 'Missing persona object' };
  }
  const persona = obj.persona as Record<string, unknown>;

  // custom_description (optional)
  if (persona.custom_description != null && typeof persona.custom_description === 'string') {
    if (persona.custom_description.length > AGENTEE_CONSTRAINTS.CUSTOM_DESCRIPTION_MAX) {
      return { ok: false, error: `custom_description exceeds ${AGENTEE_CONSTRAINTS.CUSTOM_DESCRIPTION_MAX} chars` };
    }
  }

  // core_preferences (required, all 6 dimensions, 1-5)
  if (!persona.core_preferences || typeof persona.core_preferences !== 'object') {
    return { ok: false, error: 'Missing core_preferences' };
  }
  const prefs = persona.core_preferences as Record<string, unknown>;
  for (const dim of PREFERENCE_DIMENSIONS) {
    const val = prefs[dim];
    if (typeof val !== 'number' || !Number.isInteger(val)) {
      return { ok: false, error: `core_preferences.${dim} must be an integer` };
    }
    if (val < AGENTEE_CONSTRAINTS.PREFERENCE_MIN || val > AGENTEE_CONSTRAINTS.PREFERENCE_MAX) {
      return { ok: false, error: `core_preferences.${dim} must be ${AGENTEE_CONSTRAINTS.PREFERENCE_MIN}-${AGENTEE_CONSTRAINTS.PREFERENCE_MAX}` };
    }
  }

  // custom_flavor (required array, max 3)
  if (!Array.isArray(persona.custom_flavor)) {
    return { ok: false, error: 'custom_flavor must be an array' };
  }
  if (persona.custom_flavor.length > AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_MAX) {
    return { ok: false, error: `custom_flavor exceeds max ${AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_MAX} items` };
  }
  for (const item of persona.custom_flavor) {
    if (!item || typeof item !== 'object') {
      return { ok: false, error: 'custom_flavor item must be an object' };
    }
    const flavor = item as Record<string, unknown>;
    if (typeof flavor.label !== 'string' || !flavor.label.trim()) {
      return { ok: false, error: 'custom_flavor item requires label' };
    }
    if (flavor.label.length > AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_LABEL_MAX) {
      return { ok: false, error: `custom_flavor label exceeds ${AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_LABEL_MAX} chars` };
    }
    if (typeof flavor.description !== 'string' || !flavor.description.trim()) {
      return { ok: false, error: 'custom_flavor item requires description' };
    }
    if (flavor.description.length > AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_DESC_MAX) {
      return { ok: false, error: `custom_flavor description exceeds ${AGENTEE_CONSTRAINTS.CUSTOM_FLAVOR_DESC_MAX} chars` };
    }
  }

  return {
    ok: true,
    data: input as AgenteePersonaInput,
  };
}
