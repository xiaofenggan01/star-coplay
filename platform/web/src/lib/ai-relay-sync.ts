import { callBackendInternal, extractErrorMessage } from "./backend-internal";

export interface EnsureAIRelayAccountInput {
  user_id: string;
  email?: string | null;
  display_name?: string | null;
  default_group?: string | null;
  preferred_models?: Record<string, string>;
  timeout_ms?: number;
  source?: string;
}

export interface EnsureAIRelayAccountResult {
  ok: boolean;
  status?: number;
  error?: string;
}

export async function ensureAIRelayAccount(
  input: EnsureAIRelayAccountInput,
): Promise<EnsureAIRelayAccountResult> {
  const userId = input.user_id.trim();
  if (!userId) {
    return { ok: false, error: "user_id required" };
  }

  const body: Record<string, unknown> = {};
  if (input.email?.trim()) body.email = input.email.trim().toLowerCase();
  if (input.display_name?.trim()) body.display_name = input.display_name.trim();
  if (input.default_group?.trim()) body.default_group = input.default_group.trim();
  if (input.preferred_models) body.preferred_models = input.preferred_models;

  try {
    const { payload, status } = await callBackendInternal(
      `/internal/ai/relay/users/${encodeURIComponent(userId)}/ensure`,
      {
        method: "PUT",
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(input.timeout_ms ?? 5_000),
      },
    );
    if (status < 200 || status >= 300) {
      return {
        ok: false,
        status,
        error: extractErrorMessage(payload, "AI Relay ensure failed"),
      };
    }
    return { ok: true, status };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function ensureAIRelayAccountBestEffort(
  input: EnsureAIRelayAccountInput,
): Promise<void> {
  const result = await ensureAIRelayAccount(input);
  if (result.ok) return;

  const source = input.source ? ` source=${input.source}` : "";
  const status = result.status ? ` status=${result.status}` : "";
  console.warn(
    `[ai-relay-sync] ensure failed user_id=${input.user_id}${source}${status}: ${result.error ?? "unknown error"}`,
  );
}
