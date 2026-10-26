import { defineTool } from '@deepseek-ai/dsh-tools'
import type { StarCoPlayRouteServer } from '../route/server.js'
import type { TaskBoardService } from '../tasks/board.js'
import { statusLabel, type Task } from '../tasks/model.js'

function text(value: string) {
  return {
    schema: { type: 'string' as const },
    render: (_args: unknown, output: string) => [{ type: 'text' as const, text: output }],
  }
}

function formatTask(task: Task): string {
  const parts = [
    `${task.id} · ${task.title}`,
    `状态：${statusLabel(task.status)}`,
    `完成条件：${describeCompletion(task)}`,
  ]
  if (task.progress_text) parts.push(`进度：${task.progress_text}`)
  if (task.blocked_reason) parts.push(`卡住：${task.blocked_reason}`)
  if (task.constraints.length) parts.push(`约束：${task.constraints.join('；')}`)
  return parts.join('\n')
}

function describeCompletion(task: Task): string {
  const completion = task.completion
  if (completion.type === 'inventory_at_least') {
    return `背包 ${completion.item} ≥ ${completion.qty}`
  }
  if (completion.type === 'gold_at_least') return `金币 ≥ ${completion.qty}`
  return '需要玩家确认'
}

export function registerTaskTools(
  ctx: any,
  route: StarCoPlayRouteServer,
  board: TaskBoardService,
): void {
  ctx.tools.register(defineTool({
    name: 'starcoplay_task_add',
    description: [
      '把玩家布置的事情记进任务板。玩家说“帮我做 X / 去 Y / 弄 Z 个”时调用。',
      '必须给出可验证的 completion：背包数量、金币，或 manual_confirm。',
      '记下后用 starcoplay_say 向玩家复述一句，然后开始执行。',
    ].join(' '),
    parameters: {
      title: { type: 'string', required: true, description: '一句话任务名，例如“做 20 个箱子”' },
      goal: { type: 'object', required: true, additionalProperties: true, description: '结构化目标，例如 {"type":"craft","item":"Chest","qty":20}' },
      completion: {
        type: 'object',
        required: true,
        additionalProperties: true,
        description: '完成判定，例如 {"type":"inventory_at_least","item":"Chest","qty":20} 或 {"type":"gold_at_least","qty":5000}',
      },
      constraints: { type: 'array', description: '约束，例如 ["不卖工具"]' },
      priority: { type: 'number', description: '优先级，数字越大越靠前，默认 5' },
      deadline: { type: 'object', additionalProperties: true, description: '可选截止条件，例如 {"season":"spring","day":28}' },
      derived: { type: 'array', description: '派生需求，例如 [{"type":"acquire","item":"Wood","qty":120}]' },
    },
    output: text(''),
    async execute(args: Record<string, unknown>) {
      const result = await board.add({
        title: String(args.title ?? ''),
        goal: (args.goal ?? {}) as Record<string, unknown>,
        completion: (args.completion ?? {}) as Record<string, unknown>,
        constraints: Array.isArray(args.constraints) ? args.constraints.map(String) : [],
        priority: Number(args.priority ?? 5),
        deadline: (args.deadline as Record<string, unknown>) ?? undefined,
        derived: Array.isArray(args.derived) ? (args.derived as Record<string, unknown>[]) : undefined,
      })
      if (!result.ok || !result.task) return '任务未记录：' + (result.error || '未知错误')
      return '已记录任务。\n' + formatTask(result.task) + '\n下一步：用 starcoplay_say 向玩家复述，然后开始执行。'
    },
  }))

  ctx.tools.register(defineTool({
    name: 'starcoplay_task_list',
    description: '列出任务板：进行中、排队、卡住的任务。玩家问“还有什么没做完”时调用。',
    parameters: {},
    output: text(''),
    async execute() {
      const tasks = board.list()
      if (!tasks.length) return '任务板是空的。'
      return tasks.map(formatTask).join('\n\n')
    },
  }))

  ctx.tools.register(defineTool({
    name: 'starcoplay_task_update',
    description: '修改任务：pause（暂停）、resume（继续）、cancel（取消）、activate（切换为当前任务）、modify（改标题/优先级/完成条件）。',
    parameters: {
      id: { type: 'string', required: true, description: '任务 id，例如 task_1a2b3c4d' },
      action: {
        type: 'string',
        required: true,
        description: 'pause | resume | cancel | activate | modify',
      },
      title: { type: 'string', description: 'modify 时的新标题' },
      priority: { type: 'number', description: 'modify 时的新优先级' },
      completion: { type: 'object', additionalProperties: true, description: 'modify 时的新完成条件' },
    },
    output: text(''),
    async execute(args: Record<string, unknown>) {
      const result = board.update(
        String(args.id ?? ''),
        String(args.action ?? ''),
        {
          title: args.title,
          priority: args.priority,
          completion: args.completion,
        },
      )
      if (!result.ok || !result.task) return '修改失败：' + (result.error || '未知错误')
      return '已更新。\n' + formatTask(result.task)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'starcoplay_task_report',
    description: [
      '报告任务进展：进度文本、结构化进度、证据、卡住原因。',
      '每次执行完一段动作后调用，让任务板和游戏内 HUD 保持同步。',
      'completed=true 时插件会去游戏里核对完成条件，不通过不会标记完成。',
    ].join(' '),
    parameters: {
      id: { type: 'string', required: true, description: '任务 id' },
      progress_text: { type: 'string', description: '给玩家看的一行进度，例如“木头 48/120”' },
      progress: { type: 'object', additionalProperties: true, description: '结构化进度，例如 {"wood":48}' },
      evidence: { type: 'string', description: '这次做了什么，例如“chop_trees wood_gained=24”' },
      blocked_reason: { type: 'string', description: '卡住原因；清空时传空字符串' },
      completed: { type: 'boolean', description: '认为已完成时传 true，插件会核对世界状态' },
    },
    output: text(''),
    async execute(args: Record<string, unknown>) {
      const id = String(args.id ?? '')
      const blocked = args.blocked_reason === undefined
        ? undefined
        : String(args.blocked_reason || '') || null
      const report = board.report(id, {
        progress_text: typeof args.progress_text === 'string' ? args.progress_text : undefined,
        progress: (args.progress as Record<string, unknown>) ?? undefined,
        evidence: typeof args.evidence === 'string' ? args.evidence : undefined,
        blocked_reason: blocked,
      })
      if (!report.ok || !report.task) return '报告失败：' + (report.error || '未知错误')

      if (args.completed === true) {
        const checked = await board.check(id, route)
        if (checked.ok) {
          return '完成条件已核对通过：' + checked.detail + '\n任务已完成，向玩家报告结果。'
        }
        return '还差一点：' + checked.detail + '\n任务保持进行中，继续或报告卡住原因。'
      }
      return '已更新进度。\n' + formatTask(report.task)
    },
  }))

  ctx.tools.register(defineTool({
    name: 'starcoplay_task_check',
    description: '核对某个任务的完成条件。只读，不改变游戏状态。',
    parameters: {
      id: { type: 'string', required: true, description: '任务 id' },
    },
    output: text(''),
    async execute(args: Record<string, unknown>) {
      const checked = await board.check(String(args.id ?? ''), route)
      return (checked.ok ? '已完成：' : '未完成：') + checked.detail
    },
  }))
}
