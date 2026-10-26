import { defineTool } from '@deepseek-ai/dsh-tools'
import { installMod, type InstallModOptions, type InstallModResult } from '../game/install.js'

const INSTALL_LABEL = '安装最新版'
const CANCEL_LABEL = '暂不'

function describeSelection(answer: any): string[] {
  const selected = answer?.answers?.[0]?.selected
  return Array.isArray(selected) ? selected.filter((value: unknown): value is string => typeof value === 'string') : []
}

function formatInstallResult(result: InstallModResult): string {
  const lines = [result.message]
  if (result.warnings.length > 0) {
    lines.push('', '注意：', ...result.warnings.map((warning) => '- ' + warning))
  }
  return lines.join('\n')
}

export function registerInstallModTool(
  ctx: any,
  getStatus: () => any,
  install: (options?: InstallModOptions) => Promise<InstallModResult> = installMod,
): void {
  ctx.tools.register(defineTool({
    name: 'starcoplay_install_mod',
    description: [
      'Install or update the StarCoPlay mod into the Stardew Valley Mods folder.',
      'The player sees a native confirmation dialog before anything is downloaded or written.',
      'Call this when starcoplay_status reports that the mod is missing or outdated;',
      'never claim the mod was installed until the tool result says so.',
    ].join(' '),
    parameters: {},
    output: {
      schema: { type: 'string' },
      render: (_args: unknown, value: string) => [{ type: 'text', text: value }],
    },
    async execute(_args: Record<string, unknown>, exec: any) {
      const status = getStatus()
      if (!status?.game?.gameDir) {
        return '没有找到星露谷安装目录。请先安装游戏，或在环境变量 STARCOPLAY_GAME_PATH 中指定游戏路径。'
      }
      const current = status.game.modVersion
        ? '当前版本 ' + status.game.modVersion + '。'
        : '当前没有检测到 StarCoPlay Mod。'
      const question = {
        id: 'install_mod',
        header: 'StarCoPlay',
        question: current + '是否自动安装最新版 StarCoPlay Mod？',
        options: [
          { label: INSTALL_LABEL, description: '从 GitHub Release 下载并安装到游戏的 Mods 目录' },
          { label: CANCEL_LABEL, description: '先不安装，之后可以再让我装' },
        ],
      }
      let answer: any
      try {
        const request: any = {
          questions: [question],
          signal: exec?.signal,
          wait: { callId: exec?.callId },
        }
        if (exec?.agent !== undefined) request.agent = exec.agent
        answer = await ctx.userQuestions.ask(request)
      } catch (error) {
        return '无法弹出安装确认框：' + (error instanceof Error ? error.message : String(error)) +
          '\n请手动从 GitHub Release 下载 StarCoPlay.zip，解压到 Mods/StarCoPlay。'
      }
      const selected = describeSelection(answer)
      if (!selected.includes(INSTALL_LABEL)) {
        return '已取消安装。需要的时候再叫我。'
      }
      try {
        const result = await install()
        return formatInstallResult(result)
      } catch (error) {
        return '安装失败：' + (error instanceof Error ? error.message : String(error))
      }
    },
  }))
}
