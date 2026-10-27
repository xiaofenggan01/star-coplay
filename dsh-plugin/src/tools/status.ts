import { defineTool } from '@deepseek-ai/dsh-tools'

export interface StatusSnapshot {
  pluginVersion: string
  route: {
    running: boolean
    connected: boolean
    endpoint?: string
    rendezvousPath: string
    lastError?: string
    identity?: {
      player_unique_id: string
      player_name: string
      is_ai_controlled: boolean
      target_player_id?: string | null
      revision: number
    }
  }
  game: {
    gameDir?: string
    modsDir?: string
    smapi: boolean
    smapiPath?: string
    modDir?: string
    modVersion?: string
    error?: string
  }
  launcher: { running: boolean; pid?: number }
  bridgePlayerIds: string[]
  taskHud: { supported: boolean; requiredModVersion: string }
}

export const REQUIRED_MOD_VERSION_FOR_HUD = '2.5.0'

/** Compare dotted versions; missing or malformed versions count as too old. */
export function isModVersionAtLeast(version: string | undefined, minimum: string): boolean {
  if (!version) return false
  const parse = (value: string) => value.split('.').map((part) => Number.parseInt(part, 10) || 0)
  const current = parse(version)
  const required = parse(minimum)
  const length = Math.max(current.length, required.length)
  for (let index = 0; index < length; index += 1) {
    const left = current[index] ?? 0
    const right = required[index] ?? 0
    if (left !== right) return left > right
  }
  return true
}

export function formatStatus(status: StatusSnapshot): string {
  return JSON.stringify(status, null, 2)
}

export function registerStatusTool(ctx: any, getStatus: () => StatusSnapshot): void {
  ctx.tools.register(defineTool({
    name: 'starcoplay_status',
    description: [
      'Check whether the StarCoPlay mod, SMAPI, and the local game route are ready.',
      'Call this before trying to play together. If the mod or SMAPI is missing,',
      'tell the player what to install instead of pretending the game tools exist.',
    ].join(' '),
    parameters: {},
    output: {
      schema: { type: 'string' },
      render: (_args: unknown, value: string) => [{ type: 'text', text: value }],
    },
    async execute() {
      return formatStatus(getStatus())
    },
  }))
}
