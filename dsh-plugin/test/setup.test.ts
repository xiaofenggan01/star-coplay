import { describe, expect, it } from 'vitest'
import { registerInstallModTool } from '../src/tools/setup.js'

function harness(options: {
  status: any
  selected: string[]
  installResult?: any
}) {
  const tools: any[] = []
  let asked = 0
  const calls: any[] = []
  const ctx = {
    tools: { register: (tool: any) => tools.push(tool) },
    userQuestions: {
      async ask(request: any) {
        asked += 1
        calls.push(request)
        return { answers: [{ id: 'install_mod', selected: options.selected }] }
      },
    },
  }
  const install = async () => options.installResult ?? { ok: true, message: 'installed', warnings: [] }
  registerInstallModTool(ctx, () => options.status, install)
  const tool = tools.find((entry) => entry.name === 'starcoplay_install_mod')
  return { tool, getAsked: () => asked, getCalls: () => calls }
}

const readyStatus = {
  game: { gameDir: '/game', modsDir: '/game/Mods', smapi: true },
}

describe('install mod tool', () => {
  it('cancels without installing when the player chooses no', async () => {
    const { tool, getAsked } = harness({ status: readyStatus, selected: ['暂不'] })
    const output = await tool.execute({}, { callId: 'call_1' })
    expect(getAsked()).toBe(1)
    expect(output).toContain('已取消')
  })

  it('installs after the native question is accepted', async () => {
    const { tool } = harness({
      status: readyStatus,
      selected: ['安装最新版'],
      installResult: { ok: true, message: '已安装 StarCoPlay 0.3.5', warnings: ['注意 A'] },
    })
    const output = await tool.execute({}, { callId: 'call_1' })
    expect(output).toContain('已安装 StarCoPlay 0.3.5')
    expect(output).toContain('注意 A')
  })

  it('does not ask when the game is missing', async () => {
    const { tool, getAsked } = harness({ status: { game: { smapi: false } }, selected: ['安装最新版'] })
    const output = await tool.execute({}, { callId: 'call_1' })
    expect(getAsked()).toBe(0)
    expect(output).toContain('没有找到星露谷安装目录')
  })
})
