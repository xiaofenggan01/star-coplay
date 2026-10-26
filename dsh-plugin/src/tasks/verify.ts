import type { StarCoPlayRouteServer } from '../route/server.js'
import type { LocalCommandResult } from '../route/protocol.js'
import type { CompletionSpec } from './model.js'

export interface VerifyResult {
  ok: boolean
  detail: string
}

function succeeded(result: LocalCommandResult): boolean {
  return result.state === 'SUCCEEDED' || result.state === 'OK'
}

/** Walk an unknown result for the first array stored under `items`. */
function findItems(value: unknown, depth = 0): Record<string, unknown>[] {
  if (depth > 6 || value === null || typeof value !== 'object') return []
  if (Array.isArray(value)) {
    for (const entry of value) {
      const found = findItems(entry, depth + 1)
      if (found.length) return found
    }
    return []
  }
  const record = value as Record<string, unknown>
  if (Array.isArray(record.items)) {
    return record.items.filter((entry): entry is Record<string, unknown> =>
      typeof entry === 'object' && entry !== null)
  }
  for (const entry of Object.values(record)) {
    const found = findItems(entry, depth + 1)
    if (found.length) return found
  }
  return []
}

function findNumber(value: unknown, keys: string[], depth = 0): number | null {
  if (depth > 6 || value === null || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  for (const key of keys) {
    const candidate = record[key]
    if (typeof candidate === 'number' && Number.isFinite(candidate)) return candidate
  }
  for (const entry of Object.values(record)) {
    const found = findNumber(entry, keys, depth + 1)
    if (found !== null) return found
  }
  return null
}

function normalize(value: unknown): string {
  return String(value ?? '').toLowerCase().replace(/[\s_\-()[\]（）]/g, '')
}

function itemTokens(item: Record<string, unknown>): string[] {
  return ['item_id', 'qualified_item_id', 'internal_name', 'display_name', 'name']
    .map((key) => normalize(item[key]))
    .filter(Boolean)
}

function matchesItem(item: Record<string, unknown>, target: string): boolean {
  const wanted = normalize(target)
  if (!wanted) return false
  return itemTokens(item).some((token) => token === wanted || token.includes(wanted) || wanted.includes(token))
}

function stackOf(item: Record<string, unknown>): number {
  const raw = item.stack ?? item.count ?? item.quantity ?? 1
  const value = Number(raw)
  return Number.isFinite(value) && value > 0 ? value : 1
}

/**
 * Evaluate a completion predicate against the live game.
 * Anything we cannot verify stays open; the caller decides how to report it.
 */
export async function checkCompletion(
  route: StarCoPlayRouteServer,
  completion: CompletionSpec,
): Promise<VerifyResult> {
  try {
    if (completion.type === 'inventory_at_least') {
      const result = await route.command(
        'query_inventory_snapshot',
        { container_kind: 'player', include_empty_slots: false, include_details: true },
        15000,
      )
      if (!succeeded(result)) {
        return { ok: false, detail: '背包查询失败：' + (result.error_code || result.state) }
      }
      const items = findItems(result.output)
      const count = items
        .filter((item) => matchesItem(item, completion.item || ''))
        .reduce((sum, item) => sum + stackOf(item), 0)
      const need = completion.qty || 0
      return {
        ok: count >= need,
        detail: `${completion.item} ${count}/${need}`,
      }
    }

    if (completion.type === 'gold_at_least') {
      const result = await route.command(
        'query_runtime_snapshot',
        { include_player: true, include_world_time: false, include_weather: false, include_menu_state: false },
        15000,
      )
      if (!succeeded(result)) {
        return { ok: false, detail: '金币查询失败：' + (result.error_code || result.state) }
      }
      const money = findNumber(result.output, ['money', 'gold', 'total_money'])
      if (money === null) return { ok: false, detail: '无法从游戏状态读取金币' }
      const need = completion.qty || 0
      return { ok: money >= need, detail: `金币 ${money}/${need}` }
    }

    return { ok: false, detail: '该任务需要玩家确认完成（manual_confirm）' }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    return { ok: false, detail: '无法验证：' + message }
  }
}
