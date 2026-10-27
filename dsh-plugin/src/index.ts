import { join, dirname } from 'node:path'
import { homedir } from 'node:os'
import { appendFileSync, mkdirSync } from 'node:fs'
import { randomUUID } from 'node:crypto'
import type { CapabilityEntry } from './route/digest.js'
import { detectGamePaths, listBridgePlayerIds } from './game/paths.js'
import { StarCoPlayRouteServer } from './route/server.js'
import { isModVersionAtLeast, registerStatusTool, REQUIRED_MOD_VERSION_FOR_HUD, type StatusSnapshot } from './tools/status.js'
import { registerGameTools } from './tools/game.js'
import { registerTaskTools } from './tools/tasks.js'
import { TaskBoardService } from './tasks/board.js'
import { TaskStore } from './tasks/store.js'
import { registerInstallModTool } from './tools/setup.js'
import { registerLaunchGameTool } from './tools/launch.js'
import { GameLauncher } from './game/launcher.js'
import { SYSTEM_PROMPT } from './prompt.js'
import { VERSION } from './version.js'

const DIAG_DIR = join(homedir(), '.starcoplay')
const DIAG_PATH = join(DIAG_DIR, 'dsh-plugin.log')

/**
 * The durable inbox only accepts identified user-role messages, so a bare
 * `{ content, source }` object is rejected. Prefer the host's own factory and
 * fall back to the same shape (`id` + `role`) when it is not importable.
 */
async function buildUserMessage(text: string): Promise<any> {
  const content = [{ type: 'text', text }]
  const source = { kind: 'starcoplay-dsh' }
  try {
    // Resolved from the host runtime, not from this package's dependencies.
    const specifier = '@deepseek-ai/dsh-llm'
    const llm: any = await import(specifier)
    if (llm && typeof llm.createUserMessage === 'function') {
      return llm.createUserMessage({ content, source })
    }
    diag('[message] dsh-llm has no createUserMessage export; using local shape')
  } catch (error) {
    diag('[message] dsh-llm import failed: ' + (error instanceof Error ? error.message : String(error)))
  }
  return { id: randomUUID(), role: 'user', content, source }
}

const V0_CAPABILITIES: CapabilityEntry[] = [
  ['observe_current_view', 'observe'],
  ['say', 'say'],
  ['emote', 'emote'],
  ['face', 'face'],
  ['move_to_target', 'move_to'],
  ['navigate_to_target', 'go_to'],
  ['act_on_target', 'do'],
  ['use_tool_at_target', 'use_tool'],
  ['equip_matching_item', 'equip'],
  ['sleep_for_night', 'go_bed'],
  ['water_crops', 'water_crops'],
  ['harvest_crops', 'harvest_crops'],
  ['chop_trees', 'chop_trees'],
].map(([capability_id]) => ({
  source_surface: 'v2-compat',
  capability_id,
  capability_contract_version: '2.4.5',
}))

function logInfo(ctx: any, message: string): void {
  diag('[info] ' + message)
  try {
    const logger = typeof ctx.logger === 'function' ? ctx.logger('starcoplay-dsh') : ctx.logger
    if (logger?.info) {
      logger.info(message)
      return
    }
  } catch { /* fall through */ }
  console.error('[starcoplay-dsh] ' + message)
}

/** Append-only diagnostic log the operator (and the agent) can read directly. */
export function diag(message: string): void {
  const line = '[' + new Date().toISOString() + '] ' + message + '\n'
  try {
    mkdirSync(DIAG_DIR, { recursive: true })
    appendFileSync(DIAG_PATH, line)
  } catch { /* best effort */ }
}

export const name = 'starcoplay-dsh'
export const inject = ['tools', 'systemPrompt', 'userQuestions']

export async function apply(ctx: any, config: any = {}): Promise<void> {
  const rendezvousPath = config.rendezvousPath ||
    join(homedir(), '.starcoplay', 'local-direct', 'rendezvous.json')
  const route = new StarCoPlayRouteServer({
    rendezvousPath,
    capabilityEntries: V0_CAPABILITIES,
    logger: (message) => logInfo(ctx, message),
  })
  let routeError: string | undefined
  try {
    await route.start()
  } catch (error) {
    routeError = error instanceof Error ? error.message : String(error)
    logInfo(ctx, 'route start failed: ' + routeError)
  }

  const launcher = new GameLauncher({ token: route.sessionToken })
  const getStatus = (): StatusSnapshot => {
    const game = detectGamePaths()
    return {
      pluginVersion: VERSION,
      route: { ...route.status(), lastError: routeError || route.status().lastError },
      game,
      launcher: launcher.status(),
      bridgePlayerIds: listBridgePlayerIds(game.modsDir),
      taskHud: {
        supported: isModVersionAtLeast(game.modVersion, REQUIRED_MOD_VERSION_FOR_HUD),
        requiredModVersion: REQUIRED_MOD_VERSION_FOR_HUD,
      },
    }
  }

  registerStatusTool(ctx, getStatus)
  registerInstallModTool(ctx, getStatus)
  registerLaunchGameTool(ctx, launcher)
  registerGameTools(ctx, route)

  // Task board: persistent per farmer, mirrored to the mod's in-game HUD.
  const taskBoard = new TaskBoardService({
    store: new TaskStore(),
    getPlayerId: () => getStatus().route.identity?.player_unique_id ?? null,
    onChange: (snapshot) => {
      try { route.syncTaskBoard(snapshot) } catch { /* mod not connected yet */ }
    },
  })
  registerTaskTools(ctx, route, taskBoard)
  const taskSyncTimer = setInterval(() => {
    try {
      const snapshot = taskBoard.snapshot()
      if (snapshot) route.syncTaskBoard(snapshot)
    } catch { /* best effort */ }
  }, 5000)
  taskSyncTimer.unref?.()

  // In-game chat is the real interface: the mod forwards chat_received over
  // local_direct, and we deliver it to the live agent so it can answer with
  // starcoplay_say inside the game instead of only in the DSH client.
  //
  // cordis refuses `ctx.agents` unless the plugin declared the service, and a
  // hard `inject` entry would block activation when the service is absent, so
  // bind it through a non-blocking scoped injection instead.
  let agentsApi: any
  try {
    if (typeof ctx.inject === 'function') {
      ctx.inject(['agents'], (scoped: any) => {
        agentsApi = scoped.agents
        diag('[agents] service bound')
      })
    }
  } catch (error) {
    diag('[agents] scoped injection failed: ' + (error instanceof Error ? error.message : String(error)))
  }

  const pickLiveAgent = (): { agent?: any; reason: string } => {
    try {
      if (!agentsApi) return { reason: 'agents service not bound yet' }
      const agents = agentsApi
      const roots = typeof agents.roots === 'function' ? agents.roots() : []
      const list = Array.isArray(roots) ? roots : []
      const all = typeof agents.list === 'function' ? agents.list() : []
      diag('[lookup] roots=' + list.length + ' list=' + (Array.isArray(all) ? all.length : 'n/a'))
      const candidate = list[list.length - 1] ?? (Array.isArray(all) ? all[all.length - 1] : undefined)
      if (!candidate) return { reason: 'no live agent in registry' }
      if (typeof candidate.followup === 'function') return { agent: candidate, reason: 'roots/blog.agent' }
      if (candidate.agent && typeof candidate.agent.followup === 'function') {
        return { agent: candidate.agent, reason: 'roots/blog.agent.agent' }
      }
      return { reason: 'agent has no followup()' }
    } catch (error) {
      return { reason: 'lookup threw: ' + (error instanceof Error ? error.message : String(error)) }
    }
  }

  route.onGameEvent((event) => {
    diag('[event] type=' + event.type + ' keys=' + Object.keys(event.data).join(','))
    if (event.type !== 'chat_received') return
    const text = typeof event.data.message === 'string' ? event.data.message.trim() : ''
    if (!text) return
    const name = typeof event.data.player_name === 'string' ? event.data.player_name : '玩家'
    logInfo(ctx, 'in-game chat from ' + name + ': ' + text)
    const { agent, reason } = pickLiveAgent()
    diag('[deliver] agent=' + Boolean(agent) + ' reason=' + reason)
    if (!agent) {
      logInfo(ctx, 'in-game chat dropped: ' + reason)
      return
    }
    void (async () => {
      try {
        const message = await buildUserMessage(
          '[星露谷游戏内聊天] ' + name + '：' + text +
          '\n（玩家是在游戏里说的。用 starcoplay_say 在游戏聊天里简短回一句，不要只在 DSH 里回复。）',
        )
        agent.followup(message)
        diag('[deliver] followup() accepted id=' + String(message?.id))
        try {
          const sessions = ctx.sessions
          if (sessions && typeof sessions.flush === 'function' && agent.session) {
            await sessions.flush(agent.session)
          }
        } catch (error) {
          diag('[deliver] session flush skipped: ' + (error instanceof Error ? error.message : String(error)))
        }
      } catch (error) {
        diag('[deliver] followup() threw: ' + (error instanceof Error ? error.stack ?? error.message : String(error)))
        logInfo(ctx, 'in-game chat delivery failed: ' + String(error))
      }
    })()
  })

  try {
    if (typeof ctx.systemPrompt?.section === 'function') {
      ctx.systemPrompt.section({
        name: 'starcoplay',
        order: 100,
        text: SYSTEM_PROMPT,
      })
    }
  } catch (error) {
    logInfo(ctx, 'system prompt section failed: ' + String(error))
  }

  if (typeof ctx.effect === 'function') {
    ctx.effect(() => () => { void route.stop() }, 'starcoplay-dsh')
  }
  logInfo(ctx, 'loaded v' + VERSION)
}
