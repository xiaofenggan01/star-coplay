/**
 * Agentee Persona 生成调用层
 *
 * Web 侧不直接访问 NewAPI。
 * 这里只负责通过 backend internal API 触发解析/编译。
 */

import { callBackendInternal, extractErrorMessage, BackendInternalError } from "./backend-internal";
import type {
  AgenteePersonaInput,
  PersonaCompileInput,
  PersonaCompileOutput,
  PersonaGenerateInput,
  PersonaGenerateOutput,
} from "./types/agentee";

const PARSE_PATH = "/internal/persona/parse";
const COMPILE_PATH = "/internal/persona/compile";
const PERSONA_TIMEOUT_MS = 20_000;

async function postPersonaInternal<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const { payload, status } = await callBackendInternal(path, {
    method: "POST",
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(PERSONA_TIMEOUT_MS),
  });

  if (status >= 400) {
    throw new BackendInternalError(
      extractErrorMessage(payload, "persona request failed"),
      status,
      payload,
    );
  }

  return payload as T;
}

export async function parsePersonaDraft(input: PersonaGenerateInput): Promise<PersonaGenerateOutput> {
  const payload = await postPersonaInternal<PersonaGenerateOutput & { error?: unknown }>(PARSE_PATH, {
    custom_description: input.custom_description ?? "",
    tags: input.tags ?? [],
    selected_tags: input.tags ?? [],
    persona_schema_version: 1,
  });

  return {
    core_preferences: payload.core_preferences,
    custom_flavor: payload.custom_flavor ?? [],
    generated_by_model: payload.generated_by_model,
    persona_schema_version: payload.persona_schema_version,
    generation_status: payload.generation_status,
    generation_error: payload.generation_error ?? null,
  };
}

export async function compilePersonaArtifacts(input: PersonaCompileInput): Promise<PersonaCompileOutput> {
  return postPersonaInternal<PersonaCompileOutput>(COMPILE_PATH, {
    profile: input.profile,
    persona: input.persona,
    persona_schema_version: 1,
  });
}

export function normalizePersonaDraft(input: Partial<AgenteePersonaInput>): AgenteePersonaInput | null {
  if (!input.profile || !input.persona) return null;
  return {
    profile: input.profile,
    persona: input.persona,
  };
}
