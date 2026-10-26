import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { TaskBoardService } from '../src/tasks/board.js'
import { TaskStore } from '../src/tasks/store.js'
import type { StarCoPlayRouteServer } from '../src/route/server.js'

const dirs: string[] = []

function tempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'starcoplay-tasks-'))
  dirs.push(dir)
  return dir
}

afterEach(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true })
  dirs.length = 0
})

function makeBoard(playerId = 'farmer_1') {
  const store = new TaskStore(tempDir())
  return new TaskBoardService({ store, getPlayerId: () => playerId })
}

function fakeRoute(items: Record<string, number> = {}): StarCoPlayRouteServer {
  return {
    async command(capability: string) {
      if (capability === 'query_inventory_snapshot') {
        return {
          type: 'local_command_result',
          command_id: 'cmd_1',
          state: 'SUCCEEDED',
          output: {
            items: Object.entries(items).map(([name, stack]) => ({
              internal_name: name,
              display_name: name,
              stack,
            })),
          },
        }
      }
      return { type: 'local_command_result', command_id: 'cmd_1', state: 'SUCCEEDED', output: {} }
    },
  } as unknown as StarCoPlayRouteServer
}

describe('task store', () => {
  it('round-trips a board atomically', () => {
    const dir = tempDir()
    const store = new TaskStore(dir)
    const board = {
      schema_version: 1 as const,
      player_id: 'farmer_1',
      revision: 2,
      tasks: [],
      archived: [],
    }
    store.save(board)
    expect(store.load('farmer_1')).toMatchObject({ revision: 2, player_id: 'farmer_1' })
    expect(JSON.parse(readFileSync(join(dir, 'farmer_1.json'), 'utf8')).revision).toBe(2)
  })

  it('sets a corrupt file aside instead of crashing', () => {
    const dir = tempDir()
    const store = new TaskStore(dir)
    writeFileSync(join(dir, 'farmer_1.json'), '{not json')
    const board = store.load('farmer_1')
    expect(board.tasks).toEqual([])
    expect(board.revision).toBe(0)
  })
})

describe('task board', () => {
  it('has no board without a connected farmer', async () => {
    const store = new TaskStore(tempDir())
    const board = new TaskBoardService({ store, getPlayerId: () => null })
    const result = await board.add({ title: 'x', goal: {}, completion: { type: 'manual_confirm' } })
    expect(result.ok).toBe(false)
  })

  it('makes the first task active and queues the next', async () => {
    const board = makeBoard()
    const first = await board.add({
      title: '砍 20 木头',
      goal: { type: 'acquire', item: 'Wood', qty: 20 },
      completion: { type: 'inventory_at_least', item: 'Wood', qty: 20 },
    })
    const second = await board.add({
      title: '做 20 个箱子',
      goal: { type: 'craft', item: 'Chest', qty: 20 },
      completion: { type: 'inventory_at_least', item: 'Chest', qty: 20 },
    })
    expect(first.task?.status).toBe('active')
    expect(second.task?.status).toBe('proposed')
    expect(board.list().map((task) => task.title)).toEqual(['砍 20 木头', '做 20 个箱子'])
  })

  it('activates a queued task and pauses the previous one', async () => {
    const board = makeBoard()
    const first = await board.add({
      title: 'A',
      goal: {},
      completion: { type: 'manual_confirm' },
    })
    const second = await board.add({
      title: 'B',
      goal: {},
      completion: { type: 'manual_confirm' },
    })
    board.update(second.task!.id, 'activate')
    expect(board.list().find((task) => task.id === first.task!.id)?.status).toBe('paused')
    expect(board.list().find((task) => task.id === second.task!.id)?.status).toBe('active')
  })

  it('records progress and blocked reasons', async () => {
    const board = makeBoard()
    const added = await board.add({
      title: '砍木头',
      goal: {},
      completion: { type: 'inventory_at_least', item: 'Wood', qty: 20 },
    })
    board.report(added.task!.id, {
      progress_text: '木头 8/20',
      evidence: 'chop_trees wood_gained=8',
      blocked_reason: '体力不足',
    })
    const task = board.list()[0]
    expect(task.progress_text).toBe('木头 8/20')
    expect(task.status).toBe('blocked')
    const snapshot = board.snapshot()
    expect(snapshot?.tasks[0]).toMatchObject({ progress_text: '木头 8/20', blocked_reason: '体力不足' })
  })

  it('verifies inventory before marking done', async () => {
    const board = makeBoard()
    const added = await board.add({
      title: '砍 20 木头',
      goal: { type: 'acquire', item: 'Wood', qty: 20 },
      completion: { type: 'inventory_at_least', item: 'Wood', qty: 20 },
    })
    const id = added.task!.id

    const notYet = await board.check(id, fakeRoute({ Wood: 12 }))
    expect(notYet.ok).toBe(false)
    expect(board.list()[0].status).not.toBe('done')

    const done = await board.check(id, fakeRoute({ Wood: 20 }))
    expect(done.ok).toBe(true)
    expect(board.list()).toEqual([])
    expect(board.snapshot()?.last_completed?.title).toBe('砍 20 木头')
  })
})
