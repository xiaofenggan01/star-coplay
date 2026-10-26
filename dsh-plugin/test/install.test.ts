import AdmZip from 'adm-zip'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { installMod, safeRelative } from '../src/game/install.js'
import { detectGamePaths } from '../src/game/paths.js'

const dirs: string[] = []

function tempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'starcoplay-install-'))
  dirs.push(dir)
  return dir
}

afterEach(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true })
  dirs.length = 0
})

function makeLinuxGame(): { root: string; game: string } {
  const root = tempDir()
  const game = join(root, 'Stardew Valley')
  mkdirSync(join(game, 'Mods', 'StarCoPlay', 'bridge_123'), { recursive: true })
  writeFileSync(join(game, 'StardewModdingAPI'), '')
  writeFileSync(join(game, 'Stardew Valley.dll'), '')
  writeFileSync(
    join(game, 'Mods', 'StarCoPlay', 'config.json'),
    JSON.stringify({ EnableAIControl: false, TargetPlayerId: '123', KeepMe: true }),
  )
  writeFileSync(join(game, 'Mods', 'StarCoPlay', 'bridge_123', 'keep.txt'), 'keep')
  return { root, game }
}

function makeModZip(): Buffer {
  const zip = new AdmZip()
  zip.addFile('StarCoPlay/manifest.json', Buffer.from(JSON.stringify({
    Name: 'StarCoPlay',
    UniqueID: 'StarCoPlay.Mod',
    Version: '0.3.5',
    EntryDll: 'StarCoPlay.dll',
  })))
  zip.addFile('StarCoPlay/StarCoPlay.dll', Buffer.from('new-dll'))
  zip.addFile('StarCoPlay/i18n/zh.json', Buffer.from('{}'))
  zip.addFile('StarCoPlay/config.json', Buffer.from(JSON.stringify({ EnableAIControl: false })))
  zip.addFile('StarCoPlay/bridge_123/should-not-copy.txt', Buffer.from('zip'))
  zip.addFile('StarCoPlay/logs/old.log', Buffer.from('zip'))
  return zip.toBuffer()
}

describe('mod installer', () => {
  it('installs the mod, preserves user state, and merges config', async () => {
    const { root, game } = makeLinuxGame()
    const gamePaths = detectGamePaths({
      platform: 'linux',
      home: root,
      env: { STARCOPLAY_GAME_PATH: game },
    })
    const result = await installMod({ zipBuffer: makeModZip(), gamePaths })

    expect(result.ok).toBe(true)
    expect(result.installedVersion).toBe('0.3.5')
    const modDir = join(game, 'Mods', 'StarCoPlay')
    expect(readFileSync(join(modDir, 'StarCoPlay.dll'), 'utf8')).toBe('new-dll')
    expect(existsSync(join(modDir, 'i18n', 'zh.json'))).toBe(true)
    expect(existsSync(join(modDir, 'bridge_123', 'keep.txt'))).toBe(true)
    expect(existsSync(join(modDir, 'bridge_123', 'should-not-copy.txt'))).toBe(false)
    expect(existsSync(join(modDir, 'logs', 'old.log'))).toBe(false)

    const config = JSON.parse(readFileSync(join(modDir, 'config.json'), 'utf8'))
    expect(config.EnableAIControl).toBe(true)
    expect(config.BridgeMode).toBe('auto')
    expect(config.TargetPlayerId).toBe('123')
    expect(config.KeepMe).toBe(true)
  })

  it('reports a missing game directory', async () => {
    const result = await installMod({ zipBuffer: makeModZip(), gamePaths: { smapi: false } })
    expect(result.ok).toBe(false)
    expect(result.message).toContain('Mods')
  })

  it('rejects unsafe zip-relative paths', () => {
    expect(safeRelative('../evil.txt')).toBe(false)
    expect(safeRelative('a/../../evil.txt')).toBe(false)
    expect(safeRelative('/abs/evil.txt')).toBe(false)
    expect(safeRelative('C:/evil.txt')).toBe(false)
    expect(safeRelative('i18n/zh.json')).toBe(true)
  })

  it('accepts Windows-style backslash entry names', async () => {
    const { root, game } = makeLinuxGame()
    const zip = new AdmZip()
    zip.addFile('StarCoPlay\\manifest.json', Buffer.from(JSON.stringify({
      Name: 'StarCoPlay', UniqueID: 'StarCoPlay.Mod', Version: '2.5.0', EntryDll: 'StarCoPlay.dll',
    })))
    zip.addFile('StarCoPlay\\StarCoPlay.dll', Buffer.from('win-dll'))
    const gamePaths = detectGamePaths({
      platform: 'linux', home: root, env: { STARCOPLAY_GAME_PATH: game },
    })
    const result = await installMod({ zipBuffer: zip.toBuffer(), gamePaths })
    expect(result.ok).toBe(true)
    expect(readFileSync(join(game, 'Mods', 'StarCoPlay', 'StarCoPlay.dll'), 'utf8')).toBe('win-dll')
  })

  it.skipIf(!process.env.STARCOPLAY_MOD_ZIP)(
    'installs the provided local mod zip',
    async () => {
      const { root, game } = makeLinuxGame()
      const gamePaths = detectGamePaths({
        platform: 'linux', home: root, env: { STARCOPLAY_GAME_PATH: game },
      })
      const result = await installMod({
        zipPath: process.env.STARCOPLAY_MOD_ZIP,
        gamePaths,
      })
      expect(result.ok).toBe(true)
      expect(result.installedVersion).toBe('2.6.0')
    },
    30000,
  )

  it.skipIf(process.env.STARCOPLAY_LIVE_INSTALL !== '1')(
    'downloads and installs the public release asset',
    async () => {
      const { root, game } = makeLinuxGame()
      const gamePaths = detectGamePaths({
        platform: 'linux', home: root, env: { STARCOPLAY_GAME_PATH: game },
      })
      const result = await installMod({ gamePaths })
      expect(result.ok).toBe(true)
      expect(result.installedVersion).toBe('2.6.0')
      expect(existsSync(join(game, 'Mods', 'StarCoPlay', 'StarCoPlay.dll'))).toBe(true)
    },
    45000,
  )

  it.skipIf(process.env.STARCOPLAY_LIVE_INSTALL !== '1')(
    'falls back to curl when fetch fails',
    async () => {
      const { root, game } = makeLinuxGame()
      const gamePaths = detectGamePaths({
        platform: 'linux', home: root, env: { STARCOPLAY_GAME_PATH: game },
      })
      const failingFetch = (async () => {
        throw new Error('forced fetch failure')
      }) as unknown as typeof fetch
      const result = await installMod({ gamePaths, fetchImpl: failingFetch })
      expect(result.ok).toBe(true)
      expect(result.installedVersion).toBe('2.6.0')
    },
    60000,
  )
})
