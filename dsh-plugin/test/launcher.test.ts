import { EventEmitter } from 'node:events'
import { describe, expect, it } from 'vitest'
import { GameLauncher } from '../src/game/launcher.js'
import type { GamePaths } from '../src/game/paths.js'

class FakeChild extends EventEmitter {
  pid = 4242
  exitCode: number | null = null
  killed = false
  unref(): void {}
  kill(): void { this.killed = true }
}

function paths(overrides: Partial<GamePaths> = {}): GamePaths {
  return {
    gameDir: '/game',
    smapi: true,
    smapiPath: '/game/StardewModdingAPI',
    modVersion: '2.6.0',
    ...overrides,
  }
}

describe('second instance launcher', () => {
  it('launches SMAPI from the detected game directory', async () => {
    const child = new FakeChild()
    const calls: Array<{ command: string; args: string[]; env?: NodeJS.ProcessEnv }> = []
    const launcher = new GameLauncher({
      graceMs: 10,
      token: 'route-token',
      detectPaths: () => paths(),
      spawnImpl: ((command: string, args: string[], options: { env?: NodeJS.ProcessEnv }) => {
        calls.push({ command, args, env: options.env })
        setImmediate(() => child.emit('spawn'))
        return child as any
      }) as any,
    })

    const result = await launcher.launch()
    expect(result.ok).toBe(true)
    expect(result.pid).toBe(4242)
    expect(calls[0].command).toBe('/game/StardewModdingAPI')
    expect(calls[0].args).toEqual([])
    expect(calls[0].env?.STARCOPLAY_LOCAL_DIRECT_TOKEN).toBe('route-token')
    expect(launcher.status().running).toBe(true)
  })

  it('does not start a second process when one is already tracked', async () => {
    const child = new FakeChild()
    let calls = 0
    const launcher = new GameLauncher({
      graceMs: 10,
      detectPaths: () => paths(),
      spawnImpl: (() => {
        calls += 1
        setImmediate(() => child.emit('spawn'))
        return child as any
      }) as any,
    })
    await launcher.launch()
    const second = await launcher.launch()
    expect(second.ok).toBe(true)
    expect(second.message).toContain('已经在运行')
    expect(calls).toBe(1)
  })

  it('refuses to launch without SMAPI', async () => {
    const launcher = new GameLauncher({
      detectPaths: () => paths({ smapi: false, smapiPath: undefined }),
      spawnImpl: (() => { throw new Error('should not spawn') }) as any,
    })
    const result = await launcher.launch()
    expect(result.ok).toBe(false)
    expect(result.message).toContain('SMAPI')
  })

  it('refuses an older mod build that lacks the V2 token', async () => {
    const launcher = new GameLauncher({
      detectPaths: () => paths({ modVersion: '2.5.0' }),
      spawnImpl: (() => { throw new Error('should not spawn') }) as any,
    })
    const result = await launcher.launch()
    expect(result.ok).toBe(false)
    expect(result.message).toContain('2.6.0')
  })

  it('reports an immediate spawn error', async () => {
    const child = new FakeChild()
    const launcher = new GameLauncher({
      graceMs: 50,
      detectPaths: () => paths(),
      spawnImpl: (() => {
        setImmediate(() => child.emit('error', new Error('permission denied')))
        return child as any
      }) as any,
    })
    const result = await launcher.launch()
    expect(result.ok).toBe(false)
    expect(result.message).toContain('permission denied')
    expect(launcher.status().running).toBe(false)
  })
})
