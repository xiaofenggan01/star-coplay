/** Task board data model (PRD: docs/prd-companion-task-board.md). */

export type TaskStatus =
  | 'proposed'
  | 'active'
  | 'paused'
  | 'blocked'
  | 'done'
  | 'failed'
  | 'cancelled'

export type CompletionType =
  | 'inventory_at_least'
  | 'gold_at_least'
  | 'manual_confirm'

export interface CompletionSpec {
  type: CompletionType
  item?: string
  qty?: number
}

export interface Task {
  id: string
  title: string
  source: 'player' | 'self' | 'system'
  goal: Record<string, unknown>
  derived?: Record<string, unknown>[]
  completion: CompletionSpec
  constraints: string[]
  priority: number
  deadline?: Record<string, unknown>
  status: TaskStatus
  progress: Record<string, unknown>
  progress_text?: string
  blocked_reason?: string | null
  evidence: string[]
  created_at: string
  updated_at: string
}

export interface TaskBoardFile {
  schema_version: 1
  player_id: string
  revision: number
  tasks: Task[]
  archived: Task[]
}

/** Compact line the mod renders in the in-game HUD. */
export interface HudTaskLine {
  id: string
  title: string
  status: TaskStatus
  progress_text: string
  blocked_reason: string | null
}

export interface HudSnapshot {
  type: 'task_board'
  revision: number
  updated_at: string
  tasks: HudTaskLine[]
  last_completed: { title: string; at: string } | null
}

export const COMPLETION_TYPES: CompletionType[] = [
  'inventory_at_least',
  'gold_at_least',
  'manual_confirm',
]

export function emptyBoard(playerId: string): TaskBoardFile {
  return {
    schema_version: 1,
    player_id: playerId,
    revision: 0,
    tasks: [],
    archived: [],
  }
}

export function sanitizePlayerId(playerId: string): string {
  const cleaned = String(playerId || '').replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 64)
  return cleaned || 'unknown'
}

export function normalizeCompletion(raw: unknown): CompletionSpec {
  const value = (raw ?? {}) as Record<string, unknown>
  const type = String(value.type ?? '') as CompletionType
  if (!COMPLETION_TYPES.includes(type)) {
    throw new Error(
      'completion.type must be one of: ' + COMPLETION_TYPES.join(', '),
    )
  }
  if (type === 'inventory_at_least') {
    const item = String(value.item ?? '').trim()
    const qty = Number(value.qty ?? 0)
    if (!item) throw new Error('completion.item is required for inventory_at_least')
    if (!Number.isFinite(qty) || qty <= 0) {
      throw new Error('completion.qty must be a positive number')
    }
    return { type, item, qty: Math.floor(qty) }
  }
  if (type === 'gold_at_least') {
    const qty = Number(value.qty ?? 0)
    if (!Number.isFinite(qty) || qty <= 0) {
      throw new Error('completion.qty must be a positive number')
    }
    return { type, qty: Math.floor(qty) }
  }
  return { type: 'manual_confirm' }
}

export function statusLabel(status: TaskStatus): string {
  switch (status) {
    case 'active':
      return '进行中'
    case 'proposed':
      return '排队'
    case 'paused':
      return '已暂停'
    case 'blocked':
      return '卡住'
    case 'done':
      return '完成'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '已取消'
    default:
      return status
  }
}
