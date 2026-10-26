import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { detectGamePaths, listBridgePlayerIds } from '../src/game/paths.js'

const dirs: string[] = []

function tempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'starcoplay-dsh-'))
  dirs.push(dir)
  return dir
}

afterEach(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true })
  dirs.length = 0
})

describe('game path detection', () => {
  it('detects a Linux install through the environment override', () => {
    const root = tempDir()
    const game = join(root, 'Stardew Valley')
    mkdirSync(join(game, 'Mods', 'StarCoPlay', 'bridge_123'), { recursive: true })
    writeFileSync(join(game, 'StardewModdingAPI'), '')
    writeFileSync(join(game, 'Stardew Valley.dll'), '')
    writeFileSync(join(game, 'Mods', 'StarCoPlay', 'manifest.json'), JSON.stringify({ Version: '0.3.0' }))

    const result = detectGamePaths({
      platform: 'linux',
      home: root,
      env: { STARCOPLAY_GAME_PATH: game },
    })

    expect(result.gameDir).toBe(game)
    expect(result.modsDir).toBe(join(game, 'Mods'))
    expect(result.smapi).toBe(true)
    expect(result.modVersion).toBe('0.3.0')
    expect(listBridgePlayerIds(result.modsDir)).toEqual(['123'])
  })

  it('detects the macOS Contents/MacOS layout', () => {
    const home = tempDir()
    const game = join(
      home, 'Library', 'Application Support', 'Steam', 'steamapps', 'common', 'Stardew Valley',
    )
    mkdirSync(join(game, 'Contents', 'MacOS', 'Mods', 'StarCoPlay'), { recursive: true })
    writeFileSync(join(game, 'Contents', 'MacOS', 'Stardew Valley'), '')
    writeFileSync(join(game, 'Contents', 'MacOS', 'StardewModdingAPI'), '')
    writeFileSync(
      join(game, 'Contents', 'MacOS', 'Mods', 'StarCoPlay', 'manifest.json'),
      JSON.stringify({ Version: '0.3.1' }),
    )

    const result = detectGamePaths({ platform: 'darwin', home, env: {} })

    expect(result.gameDir).toBe(game)
    expect(result.modsDir).toBe(join(game, 'Contents', 'MacOS', 'Mods'))
    expect(result.smapi).toBe(true)
    expect(result.modVersion).toBe('0.3.1')
  })

  it('reports a missing install without throwing', () => {
    const root = tempDir()
    const result = detectGamePaths({ platform: 'linux', home: root, env: {} })
    expect(result.gameDir).toBeUndefined()
    expect(result.smapi).toBe(false)
    expect(result.error).toContain('not found')
  })
})
