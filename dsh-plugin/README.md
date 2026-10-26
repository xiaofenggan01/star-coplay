# starcoplay-dsh

DeepSeek Harness plugin that lets DSH play Stardew Valley with you.

Status: early preview. The plugin loads in DSH, starts a token-gated local
route, exposes 18 native tools (13 game + 5 task), keeps a per-farmer task
board, mirrors it to the in-game HUD, installs the StarCoPlay mod after a
native confirmation dialog, launches a second SMAPI instance for co-op, and
claims the AI player automatically after it joins. Automatic co-op join is still deferred;
Windows is not verified.

## What it does

    DSH
      -> starcoplay-dsh plugin
           -> local WebSocket route (127.0.0.1)
           <- StarCoPlay mod (SMAPI)

The plugin speaks the mod's Local Direct V2 JSON protocol, so it does not need
Python, MCP, or protobuf codegen.

## Requirements

- Stardew Valley
- SMAPI 4.x (the plugin detects it but does not install it)
- The StarCoPlay mod 2.6.0 or newer (the plugin can install/update it from the
  GitHub Release asset StarCoPlay.zip)

## Install

1. Open DSH -> Plugins, or add this monorepo's plugin directory directly:
   `dsh plugin --profile desktop add file:/absolute/path/to/star-coplay/dsh-plugin`
2. Enable starcoplay-dsh. The StarCoPlay mod asset is published at this repo's
   mod-latest release tag (currently mod v2.6.0).
3. Talk to DSH. On the first turn it calls starcoplay_status. If the mod is
   missing, it calls starcoplay_install_mod, which shows a native confirmation
   dialog. Choose 安装最新版.
4. Load a save. The mod connects to the local route and the game tools appear.

## Tools

Setup:
- starcoplay_status: game, SMAPI, mod version, route and launcher state.
- starcoplay_install_mod: download/install the mod after a native confirmation.
- starcoplay_launch_game: launch a second Stardew Valley instance via SMAPI
  (no Steam single-instance lock). The player joins the co-op game in the new
  window manually until automatic join lands.

Game:
- starcoplay_observe
- starcoplay_say
- starcoplay_emote
- starcoplay_face
- starcoplay_move_to
- starcoplay_go_to
- starcoplay_do
- starcoplay_use_tool
- starcoplay_equip
- starcoplay_go_bed
- starcoplay_water_crops
- starcoplay_harvest_crops
- starcoplay_chop_trees

Tasks:
- starcoplay_task_add: record a player task with a verifiable completion.
- starcoplay_task_list: list active/queued/blocked tasks.
- starcoplay_task_update: pause, resume, cancel, activate or modify a task.
- starcoplay_task_report: write progress/evidence/blocked reason.
- starcoplay_task_check: verify a task's completion against the world.

## Task board

Tasks are stored per farmer at `~/.starcoplay/tasks/<player_unique_id>.json`
and survive plugin/game restarts. Each task carries a completion predicate
(`inventory_at_least`, `gold_at_least`, or `manual_confirm`); the plugin checks
it against the live game before marking the task done, so "done" is never
self-reported.

The board is mirrored to the mod as a compact snapshot over local_direct
(`local_task_board`). The mod renders it as a read-only top-left HUD strip and
falls back to nothing when the mod is missing or too old.

## Co-op second window (V2)

1. Open your own co-op save as the host (Steam or SMAPI is fine).
2. Ask DSH to open a second window; it calls starcoplay_launch_game.
3. In the new window choose Co-op -> Join LAN -> your farm -> a cabin.
4. Once the AI player is in the world, the mod sends its player identity. The
   plugin claims control of that player automatically; no /agent_players or
   /agent_control commands are needed.

The launched instance carries a one-shot route token; the host's Steam/SMAPI
instance does not have it, so the host cannot steal the local_direct route.
Automatic co-op join (the Join LAN click) remains deferred.

## Development

    pnpm install
    pnpm test
    pnpm typecheck
    pnpm build

Commit lib/index.js when publishing: DSH installs from GitHub without running
build scripts, so the built bundle must be in the repository.

Load the local checkout into a DSH profile. pnpm's git specs resolve the repo
root, so they cannot address this subdirectory; file: is the supported path
while the plugin lives in the monorepo:

    dsh plugin --profile desktop add file:/absolute/path/to/star-coplay/dsh-plugin

For a local mod zip without a GitHub release, set STARCOPLAY_MOD_ZIP to the
zip path before starting DSH. The installer will use that file instead of the
latest release asset.

After rebuilding, remove the profile's node_modules copy and re-add the package
(pnpm caches file: dependencies by content).

## Architecture

- src/route: rendezvous file, token-gated WebSocket server, local_direct
  handshake, identity/control frames, V2 JSON command/result frames,
  capability digest, serial command queue.
- src/game: Steam/game/Mods/SMAPI detection, mod install, and the second
  SMAPI instance launcher.
- src/tools: DSH native tools, including the install confirmation flow.
- src/prompt.ts: companion system prompt.

## Releasing

The plugin and the mod are released separately:

1. Build the mod from `mod/` (Build & Release workflow).
2. Upload the zip to the stable mod-latest tag:

    gh release upload mod-latest /path/StarCoPlay.zip \
      --repo xiaofenggan01/star-coplay --clobber

3. Bump `dsh-plugin/package.json` and `src/version.ts`, run `pnpm build`,
   commit `lib/index.js`.
4. Tag the plugin and create a GitHub release for the source snapshot.

## Known limitations

- SMAPI is detected, not auto-installed.
- The mod asset lives at this repo's mod-latest release tag; upload a new
  StarCoPlay.zip there with --clobber when the mod updates.
- Version comparison/update prompts are basic; the installer always installs
  the latest asset.
- Windows paths and file locks are not verified yet.
- The first-run prompt happens on the first chat turn, not at plugin activation.
- Automatic co-op join is deferred; the second window starts at the title screen.
- Mod 2.6.0 is required for the token-gated route and automatic target claim;
  older mod builds fall back to the WS bridge and cannot connect to this route.

## Security

- The route binds 127.0.0.1 only.
- The rendezvous file is 0600 and contains a random proof.
- The installer rejects unsafe zip paths and preserves config.json, bridge_*,
  logs, and test-reports.
- The plugin never installs SMAPI automatically and never executes shell
  commands from the model.

## License

MIT
