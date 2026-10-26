import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import {
  emptyBoard,
  sanitizePlayerId,
  type TaskBoardFile,
} from './model.js'

const DEFAULT_DIR = join(homedir(), '.starcoplay', 'tasks')

/** JSON store, one file per farmer/save. Atomic writes; corrupt files are set aside. */
export class TaskStore {
  constructor(private readonly dir: string = DEFAULT_DIR) {}

  pathFor(playerId: string): string {
    return join(this.dir, sanitizePlayerId(playerId) + '.json')
  }

  load(playerId: string): TaskBoardFile {
    const path = this.pathFor(playerId)
    if (!existsSync(path)) return emptyBoard(playerId)
    try {
      const parsed = JSON.parse(readFileSync(path, 'utf8')) as Partial<TaskBoardFile>
      if (!parsed || typeof parsed !== 'object' || !Array.isArray(parsed.tasks)) {
        throw new Error('invalid task board shape')
      }
      return {
        schema_version: 1,
        player_id: playerId,
        revision: Number.isFinite(parsed.revision) ? Number(parsed.revision) : 0,
        tasks: parsed.tasks as TaskBoardFile['tasks'],
        archived: Array.isArray(parsed.archived) ? parsed.archived : [],
      }
    } catch {
      // Keep the damaged file for inspection instead of silently deleting it.
      try {
        renameSync(path, path + '.corrupt-' + Date.now())
      } catch { /* best effort */ }
      return emptyBoard(playerId)
    }
  }

  save(board: TaskBoardFile): void {
    mkdirSync(this.dir, { recursive: true })
    const path = this.pathFor(board.player_id)
    const tmp = path + '.tmp'
    writeFileSync(tmp, JSON.stringify(board, null, 2), 'utf8')
    renameSync(tmp, path)
  }
}
