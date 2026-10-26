import { defineTool } from '@deepseek-ai/dsh-tools'
import type { GameLauncher } from '../game/launcher.js'

export function registerLaunchGameTool(ctx: any, launcher: GameLauncher): void {
  ctx.tools.register(defineTool({
    name: 'starcoplay_launch_game',
    description: [
      'Launch a SECOND Stardew Valley instance through SMAPI on this machine.',
      'Use it after the player has opened the co-op save in their own game; it does not launch or host the human instance.',
      'The new window starts at the title screen. Automatic co-op join is not enabled yet, so the player still',
      'joins the LAN/friend game once in that window. After joining, the mod connects to this plugin automatically.',
    ].join(' '),
    parameters: {},
    output: {
      schema: { type: 'string' },
      render: (_args: unknown, value: string) => [{ type: 'text', text: value }],
    },
    async execute() {
      const result = await launcher.launch()
      return result.message
    },
  }))
}
