import { describe, expect, it } from 'vitest'
import { formatGameResult, registerGameTools } from '../src/tools/game.js'
import type { LocalCommandResult } from '../src/route/protocol.js'

function result(observation: string, state = 'SUCCEEDED'): LocalCommandResult {
  return {
    type: 'local_command_result',
    command_id: 'cmd_1',
    state,
    output: { success: state === 'SUCCEEDED', observation },
  }
}

class FakeRoute {
  calls: Array<{ capability: string; params: Record<string, unknown> }> = []
  response: LocalCommandResult = result('ok')

  async command(capability: string, params: Record<string, unknown>): Promise<LocalCommandResult> {
    this.calls.push({ capability, params })
    return this.response
  }
}

function registerAll(route: FakeRoute) {
  const tools: any[] = []
  registerGameTools({ tools: { register: (tool: any) => tools.push(tool) } }, route as any)
  return tools
}

describe('native game tools', () => {
  it('registers the v0 tool surface', () => {
    const tools = registerAll(new FakeRoute())
    const names = tools.map((tool) => tool.name)
    expect(names).toContain('starcoplay_observe')
    expect(names).toContain('starcoplay_say')
    expect(names).toContain('starcoplay_go_to')
    expect(names).toContain('starcoplay_water_crops')
    expect(names).toContain('starcoplay_chop_trees')
    expect(names).toHaveLength(13)
  })

  it('maps say to the V2 capability and formats the observation', async () => {
    const route = new FakeRoute()
    const tools = registerAll(route)
    const say = tools.find((tool) => tool.name === 'starcoplay_say')
    const output = await say.execute({ message: 'hello' }, {})
    expect(route.calls).toEqual([{ capability: 'say', params: { message: 'hello' } }])
    expect(output).toBe('ok')
  })

  it('rejects an empty say without calling the game', async () => {
    const route = new FakeRoute()
    const tools = registerAll(route)
    const say = tools.find((tool) => tool.name === 'starcoplay_say')
    const output = await say.execute({ message: '   ' }, {})
    expect(route.calls).toHaveLength(0)
    expect(output).toContain('不能为空')
  })

  it('formats failed command results', () => {
    const failed: LocalCommandResult = {
      type: 'local_command_result',
      command_id: 'cmd_1',
      state: 'FAILED',
      error_code: 'TIMEOUT',
      output: { success: false, observation: 'too far' },
    }
    const text = formatGameResult(failed)
    expect(text).toContain('TIMEOUT')
    expect(text).toContain('too far')
  })
})
