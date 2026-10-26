import { randomUUID } from 'node:crypto'
import type { StarCoPlayRouteServer } from '../route/server.js'
import {
  normalizeCompletion,
  statusLabel,
  type CompletionSpec,
  type HudSnapshot,
  type HudTaskLine,
  type Task,
  type TaskBoardFile,
  type TaskStatus,
} from './model.js'
import type { TaskStore } from './store.js'
import { checkCompletion, type VerifyResult } from './verify.js'

export interface TaskBoardOptions {
  store: TaskStore
  /** Current farmer/save id, or null when the game is not connected. */
  getPlayerId: () => string | null
  onChange?: (snapshot: HudSnapshot) => void
  now?: () => Date
}

export interface AddTaskInput {
  title: string
  goal: Record<string, unknown>
  completion: CompletionSpec | Record<string, unknown>
  derived?: Record<string, unknown>[]
  constraints?: string[]
  priority?: number
  deadline?: Record<string, unknown>
}

const HUD_LIMIT = 8
const ACTIVE_STATUSES: TaskStatus[] = ['active', 'blocked', 'paused', 'proposed']

function sortForDisplay(tasks: Task[]): Task[] {
  const rank: Record<string, number> = { active: 0, blocked: 1, paused: 2, proposed: 3 }
  return [...tasks].sort((a, b) => {
    const byStatus = (rank[a.status] ?? 9) - (rank[b.status] ?? 9)
    if (byStatus !== 0) return byStatus
    if (a.priority !== b.priority) return b.priority - a.priority
    return a.created_at.localeCompare(b.created_at)
  })
}

/** In-memory board with JSON persistence and a HUD snapshot for the mod. */
export class TaskBoardService {
  private board: TaskBoardFile | null = null
  private lastCompleted: { title: string; at: string } | null = null
  private notifyTimer: NodeJS.Timeout | null = null

  constructor(private readonly options: TaskBoardOptions) {}

  private clock(): Date {
    return this.options.now ? this.options.now() : new Date()
  }

  private ensure(): TaskBoardFile | null {
    const playerId = this.options.getPlayerId()
    if (!playerId) return null
    if (!this.board || this.board.player_id !== playerId) {
      this.board = this.options.store.load(playerId)
    }
    return this.board
  }

  status(): { connected: boolean; player_id: string | null; tasks: number } {
    const board = this.ensure()
    return {
      connected: board !== null,
      player_id: board?.player_id ?? null,
      tasks: board?.tasks.length ?? 0,
    }
  }

  async add(input: AddTaskInput): Promise<{ ok: boolean; task?: Task; error?: string }> {
    const board = this.ensure()
    if (!board) return { ok: false, error: '游戏尚未连接：先读档再布置任务。' }

    const title = String(input.title || '').trim()
    if (!title) return { ok: false, error: 'title 不能为空' }
    if (!input.goal || typeof input.goal !== 'object') {
      return { ok: false, error: 'goal 必须是对象' }
    }

    let completion: CompletionSpec
    try {
      completion = normalizeCompletion(input.completion)
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : String(error) }
    }

    const now = this.clock().toISOString()
    const hasActive = board.tasks.some((task) => task.status === 'active')
    const task: Task = {
      id: 'task_' + randomUUID().slice(0, 8),
      title,
      source: 'player',
      goal: input.goal,
      derived: Array.isArray(input.derived) ? input.derived : undefined,
      completion,
      constraints: Array.isArray(input.constraints) ? input.constraints.map(String) : [],
      priority: Number.isFinite(input.priority) ? Number(input.priority) : 5,
      deadline: input.deadline,
      status: hasActive ? 'proposed' : 'active',
      progress: {},
      progress_text: '',
      blocked_reason: null,
      evidence: [],
      created_at: now,
      updated_at: now,
    }
    board.tasks.push(task)
    this.persist(board)
    return { ok: true, task }
  }

  list(): Task[] {
    const board = this.ensure()
    if (!board) return []
    return sortForDisplay(board.tasks.filter((task) =>
      !['done', 'failed', 'cancelled'].includes(task.status)))
  }

  update(
    id: string,
    action: string,
    patch: Record<string, unknown> = {},
  ): { ok: boolean; task?: Task; error?: string } {
    const board = this.ensure()
    if (!board) return { ok: false, error: '游戏尚未连接' }
    const task = board.tasks.find((entry) => entry.id === id)
    if (!task) return { ok: false, error: '找不到任务 ' + id }

    const now = this.clock().toISOString()
    switch (action) {
      case 'pause':
        if (task.status === 'active') task.status = 'paused'
        break
      case 'resume':
        if (task.status === 'paused' || task.status === 'blocked') {
          this.pauseOthers(board, task.id)
          task.status = 'active'
          task.blocked_reason = null
        }
        break
      case 'cancel':
        task.status = 'cancelled'
        break
      case 'activate':
        this.pauseOthers(board, task.id)
        task.status = 'active'
        task.blocked_reason = null
        break
      case 'modify':
        if (typeof patch.title === 'string' && patch.title.trim()) task.title = patch.title.trim()
        if (Number.isFinite(Number(patch.priority))) task.priority = Number(patch.priority)
        if (patch.deadline && typeof patch.deadline === 'object') {
          task.deadline = patch.deadline as Record<string, unknown>
        }
        if (patch.completion) {
          try {
            task.completion = normalizeCompletion(patch.completion)
          } catch (error) {
            return { ok: false, error: error instanceof Error ? error.message : String(error) }
          }
        }
        break
      default:
        return { ok: false, error: '未知操作：' + action }
    }

    task.updated_at = now
    if (task.status === 'cancelled') this.activateNext(board)
    this.persist(board)
    return { ok: true, task }
  }

  report(
    id: string,
    input: { progress_text?: string; progress?: Record<string, unknown>; evidence?: string; blocked_reason?: string | null },
  ): { ok: boolean; task?: Task; error?: string } {
    const board = this.ensure()
    if (!board) return { ok: false, error: '游戏尚未连接' }
    const task = board.tasks.find((entry) => entry.id === id)
    if (!task) return { ok: false, error: '找不到任务 ' + id }

    if (typeof input.progress_text === 'string') task.progress_text = input.progress_text
    if (input.progress && typeof input.progress === 'object') {
      task.progress = { ...task.progress, ...input.progress }
    }
    if (typeof input.evidence === 'string' && input.evidence.trim()) {
      task.evidence.push(input.evidence.trim())
      if (task.evidence.length > 20) task.evidence.shift()
    }
    if (input.blocked_reason !== undefined) {
      task.blocked_reason = input.blocked_reason
      if (input.blocked_reason) {
        task.status = 'blocked'
      } else if (task.status === 'blocked') {
        task.status = 'active'
      }
    }
    task.updated_at = this.clock().toISOString()
    this.persist(board)
    return { ok: true, task }
  }

  async check(id: string, route: StarCoPlayRouteServer): Promise<{ ok: boolean; detail: string; task?: Task }> {
    const board = this.ensure()
    if (!board) return { ok: false, detail: '游戏尚未连接' }
    const task = board.tasks.find((entry) => entry.id === id)
    if (!task) return { ok: false, detail: '找不到任务 ' + id }

    const result: VerifyResult = await checkCompletion(route, task.completion)
    const now = this.clock().toISOString()
    task.updated_at = now
    if (result.ok) {
      task.status = 'done'
      task.progress_text = task.progress_text || result.detail
      this.lastCompleted = { title: task.title, at: now }
      board.archived.push(task)
      board.tasks = board.tasks.filter((entry) => entry.id !== task.id)
      this.activateNext(board)
    } else if (task.status !== 'blocked') {
      task.blocked_reason = result.detail
    }
    this.persist(board)
    return { ok: result.ok, detail: result.detail, task }
  }

  snapshot(): HudSnapshot | null {
    const board = this.ensure()
    if (!board) return null
    const lines: HudTaskLine[] = sortForDisplay(
      board.tasks.filter((task) => ACTIVE_STATUSES.includes(task.status)),
    ).slice(0, HUD_LIMIT).map((task) => ({
      id: task.id,
      title: task.title,
      status: task.status,
      progress_text: task.progress_text || statusLabel(task.status),
      blocked_reason: task.blocked_reason ?? null,
    }))
    return {
      type: 'task_board',
      revision: board.revision,
      updated_at: this.clock().toISOString(),
      tasks: lines,
      last_completed: this.lastCompleted,
    }
  }

  private pauseOthers(board: TaskBoardFile, keepId: string): void {
    for (const entry of board.tasks) {
      if (entry.id !== keepId && entry.status === 'active') entry.status = 'paused'
    }
  }

  private activateNext(board: TaskBoardFile): void {
    if (board.tasks.some((entry) => entry.status === 'active')) return
    const next = sortForDisplay(board.tasks.filter((entry) => entry.status === 'proposed'))[0]
    if (next) {
      next.status = 'active'
      next.updated_at = this.clock().toISOString()
    }
  }

  private persist(board: TaskBoardFile): void {
    board.revision += 1
    this.options.store.save(board)
    this.scheduleNotify()
  }

  private scheduleNotify(): void {
    if (!this.options.onChange || this.notifyTimer) return
    this.notifyTimer = setTimeout(() => {
      this.notifyTimer = null
      const snapshot = this.snapshot()
      if (snapshot) this.options.onChange?.(snapshot)
    }, 50)
    this.notifyTimer.unref?.()
  }
}
