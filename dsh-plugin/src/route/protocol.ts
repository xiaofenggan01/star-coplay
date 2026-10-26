import type { CapabilityEntry } from './digest.js'

export interface LocalDirectFence {
  context_ref: string
  principal_ref: string
  runtime_ref: string
  game_session_ref: string
  game_instance_nonce: string
  generation: number
  binding_ref: string
  binding_version: number
  lease_epoch: number
  capability_digest: string
}

export interface RendezvousMaterial extends LocalDirectFence {
  endpoint: string
  proof: string
  capability_entries: CapabilityEntry[]
  require_token?: boolean
  token?: string
}

export interface LocalCommandFrame {
  type: 'local_command'
  fence: LocalDirectFence
  command_id: string
  payload: {
    capability: string
    params: Record<string, unknown>
  }
  wire_format: 'v2-json'
  deadline_at: number
}

export interface LocalCommandResult {
  type: 'local_command_result'
  command_id: string
  state: string
  output?: unknown
  error_code?: string | null
  accepted?: boolean | null
  control_id?: string | null
}

export interface LocalIdentity {
  player_unique_id: string
  player_name: string
  is_ai_controlled: boolean
  target_player_id?: string | null
  revision: number
}

export interface LocalControlFrame {
  type: 'local_control'
  fence: LocalDirectFence
  enabled: boolean
  target_player_id: string
  revision: number
}

const TERMINAL_STATES = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'TIMEOUT'])

export function fenceOf(material: LocalDirectFence): LocalDirectFence {
  return {
    context_ref: material.context_ref,
    principal_ref: material.principal_ref,
    runtime_ref: material.runtime_ref,
    game_session_ref: material.game_session_ref,
    game_instance_nonce: material.game_instance_nonce,
    generation: material.generation,
    binding_ref: material.binding_ref,
    binding_version: material.binding_version,
    lease_epoch: material.lease_epoch,
    capability_digest: material.capability_digest,
  }
}

export function buildLocalCommand(
  material: LocalDirectFence,
  capability: string,
  params: Record<string, unknown>,
  commandId: string,
  timeoutMs: number,
  nowMs = Date.now(),
): LocalCommandFrame {
  if (!capability) throw new Error('capability is required')
  if (!commandId) throw new Error('commandId is required')
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('timeoutMs must be positive')
  return {
    type: 'local_command',
    fence: fenceOf(material),
    command_id: commandId,
    payload: { capability, params: params ?? {} },
    wire_format: 'v2-json',
    deadline_at: (nowMs + timeoutMs) / 1000,
  }
}

export function buildLocalControl(
  material: LocalDirectFence,
  playerId: string,
  revision: number,
): LocalControlFrame {
  if (!playerId) throw new Error('playerId is required')
  if (!Number.isSafeInteger(revision) || revision < 0) throw new Error('revision must be a non-negative integer')
  return {
    type: 'local_control',
    fence: fenceOf(material),
    enabled: true,
    target_player_id: playerId,
    revision,
  }
}

export function isLocalIdentity(value: unknown): value is LocalIdentity {
  if (typeof value !== 'object' || value === null) return false
  const frame = value as Record<string, unknown>
  return frame.type === 'local_identity' &&
    typeof frame.player_unique_id === 'string' &&
    frame.player_unique_id.length > 0 &&
    (frame.revision === undefined || typeof frame.revision === 'number')
}

export function isTerminalState(state: string): boolean {
  return TERMINAL_STATES.has(state)
}

export function isLocalCommandResult(value: unknown): value is LocalCommandResult {
  if (typeof value !== 'object' || value === null) return false
  const frame = value as Record<string, unknown>
  return frame.type === 'local_command_result' && typeof frame.command_id === 'string'
}
