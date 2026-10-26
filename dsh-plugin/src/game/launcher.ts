import { spawn, type ChildProcess } from 'node:child_process'
import { dirname } from 'node:path'
import { detectGamePaths, type GamePaths } from './paths.js'

export const MIN_MOD_VERSION = '2.6.0'

export interface GameLaunchResult {
  ok: boolean
  message: string
  pid?: number
}

function versionParts(value: string): number[] {
  const match = value.match(/\d+(?:\.\d+)*/)?.[0] ?? ''
  return match.split('.').map((part) => Number.parseInt(part, 10) || 0)
}

export function versionAtLeast(current: string, minimum: string): boolean {
  const a = versionParts(current)
  const b = versionParts(minimum)
  const length = Math.max(a.length, b.length)
  for (let index = 0; index < length; index += 1) {
    const left = a[index] ?? 0
    const right = b[index] ?? 0
    if (left > right) return true
    if (left < right) return false
  }
  return true
}

export interface LauncherDeps {
  spawnImpl?: typeof spawn
  detectPaths?: () => GamePaths
  graceMs?: number
  token?: string
}

export class GameLauncher {
  private child?: ChildProcess
  private readonly spawnImpl: typeof spawn
  private readonly detectPaths: () => GamePaths
  private readonly graceMs: number
  private readonly token?: string

  constructor(deps: LauncherDeps = {}) {
    this.spawnImpl = deps.spawnImpl ?? spawn
    this.detectPaths = deps.detectPaths ?? (() => detectGamePaths())
    this.graceMs = Math.max(0, deps.graceMs ?? 1200)
    this.token = deps.token
  }

  status(): { running: boolean; pid?: number } {
    const running = Boolean(this.child && this.child.exitCode === null && !this.child.killed)
    return running
      ? { running: true, pid: this.child?.pid }
      : { running: false }
  }

  async launch(paths?: GamePaths): Promise<GameLaunchResult> {
    const current = paths ?? this.detectPaths()
    if (!current.gameDir) {
      return { ok: false, message: '没有找到星露谷安装目录。请先安装游戏，或设置 STARCOPLAY_GAME_PATH。' }
    }
    if (!current.smapiPath) {
      return { ok: false, message: '没有检测到 SMAPI。第二个实例需要通过 SMAPI 启动，请先安装 SMAPI 4.x。' }
    }
    if (!current.modVersion) {
      return { ok: false, message: '没有检测到 StarCoPlay Mod。请先安装 Mod ' + MIN_MOD_VERSION + ' 或更新。' }
    }
    if (!versionAtLeast(current.modVersion, MIN_MOD_VERSION)) {
      return {
        ok: false,
        message: '当前 Mod 版本 ' + current.modVersion + '，V2 需要 ' + MIN_MOD_VERSION +
          ' 才支持实例 token 和自动目标；请先运行 Mod 安装/更新。',
      }
    }
    const running = this.status()
    if (running.running) {
      return { ok: true, message: '第二个星露谷实例已经在运行（PID=' + running.pid + '）。', pid: running.pid }
    }

    let child: ChildProcess
    try {
      child = this.spawnImpl(current.smapiPath, [], {
        cwd: dirname(current.smapiPath),
        detached: true,
        stdio: 'ignore',
        windowsHide: false,
        env: this.token
          ? { ...process.env, STARCOPLAY_LOCAL_DIRECT_TOKEN: this.token }
          : process.env,
      })
    } catch (error) {
      return { ok: false, message: '启动 SMAPI 失败：' + (error instanceof Error ? error.message : String(error)) }
    }
    this.child = child

    return await new Promise<GameLaunchResult>((resolve) => {
      let settled = false
      const finish = (result: GameLaunchResult) => {
        if (settled) return
        settled = true
        resolve(result)
      }
      const timer = setTimeout(() => {
        try { child.unref() } catch { /* ignore */ }
        finish({
          ok: true,
          message: '已启动第二个星露谷实例（SMAPI）。请在新窗口里加入联机存档；加入后路线会自动连上。',
          pid: child.pid,
        })
      }, this.graceMs)
      timer.unref?.()

      child.once('error', (error) => {
        clearTimeout(timer)
        if (this.child === child) this.child = undefined
        finish({ ok: false, message: '启动 SMAPI 失败：' + error.message })
      })
      child.once('spawn', () => {
        try { child.unref() } catch { /* ignore */ }
      })
      child.once('exit', (code) => {
        clearTimeout(timer)
        if (this.child === child) this.child = undefined
        if (!settled && code !== null && code !== 0) {
          finish({ ok: false, message: 'SMAPI 启动后立即退出（code=' + code + '）。' })
        }
      })
    })
  }

  stop(): void {
    const child = this.child
    if (!child || child.exitCode !== null) {
      this.child = undefined
      return
    }
    try { child.kill() } catch { /* ignore */ }
    this.child = undefined
  }
}
