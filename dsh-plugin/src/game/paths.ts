import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'

export interface GameEnvironment {
  platform?: NodeJS.Platform
  home?: string
  env?: NodeJS.ProcessEnv
}

export interface GamePaths {
  gameDir?: string
  modsDir?: string
  smapi: boolean
  smapiPath?: string
  modDir?: string
  modVersion?: string
  error?: string
}

const GAME_NAME = 'Stardew Valley'

function unique(values: Array<string | undefined | null>): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value)))]
}

function steamRoots(platform: NodeJS.Platform, home: string, env: NodeJS.ProcessEnv): string[] {
  const roots = [
    env.STEAM_PATH,
    join(home, 'Library', 'Application Support', 'Steam'),
    join(home, '.local', 'share', 'Steam'),
    join(home, '.steam', 'steam'),
    'C:\\Program Files (x86)\\Steam',
    'C:\\Program Files\\Steam',
  ]
  if (platform === 'win32') {
    if (env['ProgramFiles(x86)']) roots.push(join(env['ProgramFiles(x86)'], 'Steam'))
    if (env.ProgramFiles) roots.push(join(env.ProgramFiles, 'Steam'))
  }
  return unique(roots)
}

function parseLibraryFolders(steamRoot: string): string[] {
  const vdf = join(steamRoot, 'steamapps', 'libraryfolders.vdf')
  if (!existsSync(vdf)) return []
  try {
    const text = readFileSync(vdf, 'utf8')
    const paths: string[] = []
    for (const match of text.matchAll(/"path"\s+"([^"]+)"/g)) {
      paths.push(match[1].replace(/\\\\/g, '\\'))
    }
    return paths
  } catch {
    return []
  }
}

function gameCandidates(platform: NodeJS.Platform, home: string, env: NodeJS.ProcessEnv): string[] {
  const candidates: string[] = []
  if (env.STARCOPLAY_GAME_PATH) candidates.push(env.STARCOPLAY_GAME_PATH)
  for (const root of steamRoots(platform, home, env)) {
    candidates.push(join(root, 'steamapps', 'common', GAME_NAME))
    for (const library of parseLibraryFolders(root)) {
      candidates.push(join(library, 'steamapps', 'common', GAME_NAME))
    }
  }
  return unique(candidates)
}

function isGameDir(dir: string, platform: NodeJS.Platform): boolean {
  if (platform === 'darwin') {
    return existsSync(join(dir, 'Contents', 'MacOS', 'Stardew Valley'))
  }
  if (platform === 'win32') {
    return existsSync(join(dir, 'Stardew Valley.exe')) ||
      existsSync(join(dir, 'StardewValley.exe'))
  }
  return existsSync(join(dir, 'Stardew Valley.dll')) ||
    existsSync(join(dir, 'StardewValley')) ||
    existsSync(join(dir, 'Content'))
}

export function modsDirFor(gameDir: string, platform: NodeJS.Platform): string {
  if (platform === 'darwin') return join(gameDir, 'Contents', 'MacOS', 'Mods')
  return join(gameDir, 'Mods')
}

function smapiPathFor(gameDir: string, platform: NodeJS.Platform): string {
  if (platform === 'darwin') return join(gameDir, 'Contents', 'MacOS', 'StardewModdingAPI')
  if (platform === 'win32') return join(gameDir, 'StardewModdingAPI.exe')
  return join(gameDir, 'StardewModdingAPI')
}

function readModVersion(modDir: string): string | undefined {
  const manifest = join(modDir, 'manifest.json')
  if (!existsSync(manifest)) return undefined
  try {
    const parsed = JSON.parse(readFileSync(manifest, 'utf8')) as Record<string, unknown>
    return typeof parsed.Version === 'string' ? parsed.Version : undefined
  } catch {
    return undefined
  }
}

export function detectGamePaths(environment: GameEnvironment = {}): GamePaths {
  const platform = environment.platform ?? process.platform
  const home = environment.home ?? homedir()
  const env = environment.env ?? process.env
  const explicitMods = env.STARCOPLAY_MODS_PATH
  if (explicitMods) {
    const modDir = join(explicitMods, 'StarCoPlay')
    return {
      modsDir: explicitMods,
      smapi: false,
      modDir,
      modVersion: readModVersion(modDir),
    }
  }
  const gameDir = gameCandidates(platform, home, env).find((candidate) => isGameDir(candidate, platform))
  if (!gameDir) {
    return { smapi: false, error: 'Stardew Valley installation was not found' }
  }
  const modsDir = modsDirFor(gameDir, platform)
  const modDir = join(modsDir, 'StarCoPlay')
  const smapiPath = smapiPathFor(gameDir, platform)
  return {
    gameDir,
    modsDir,
    smapi: existsSync(smapiPath),
    smapiPath: existsSync(smapiPath) ? smapiPath : undefined,
    modDir,
    modVersion: readModVersion(modDir),
  }
}

export function listBridgePlayerIds(modsDir: string | undefined): string[] {
  if (!modsDir) return []
  const starcoplayDir = join(modsDir, 'StarCoPlay')
  if (!existsSync(starcoplayDir)) return []
  try {
    return readdirSync(starcoplayDir)
      .map((name) => /^bridge_(\d+)$/.exec(name)?.[1])
      .filter((value): value is string => Boolean(value))
  } catch {
    return []
  }
}
