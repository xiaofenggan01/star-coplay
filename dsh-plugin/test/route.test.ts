import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { WebSocket } from 'ws'
import { StarCoPlayRouteServer } from '../src/route/server.js'
import type { CapabilityEntry } from '../src/route/digest.js'

const dirs: string[] = []
const entries: CapabilityEntry[] = [{
  source_surface: 'v2-compat',
  capability_id: 'say',
  capability_contract_version: '2.4.5',
}]

function tempDir(): string {
  const dir = mkdtempSync(join(tmpdir(), 'starcoplay-route-'))
  dirs.push(dir)
  return dir
}

afterEach(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true })
  dirs.length = 0
})

function nextMessage(socket: WebSocket): Promise<string> {
  return new Promise((resolve, reject) => {
    socket.once('message', (data) => resolve(data.toString()))
    socket.once('error', reject)
  })
}

function waitOpen(socket: WebSocket): Promise<void> {
  return new Promise((resolve, reject) => {
    socket.once('open', () => resolve())
    socket.once('error', reject)
  })
}

describe('local route server', () => {
  it('accepts a local_direct hello and completes a V2 command', async () => {
    const dir = tempDir()
    const rendezvousPath = join(dir, 'rendezvous.json')
    const server = new StarCoPlayRouteServer({ rendezvousPath, capabilityEntries: entries })
    await server.start()
    const material = JSON.parse(readFileSync(rendezvousPath, 'utf8')) as Record<string, unknown>
    expect(String(material.endpoint)).toMatch(/^ws:\/\/127\.0\.0\.1:\d+$/)
    expect(material.require_token).toBe(true)
    expect(typeof material.token).toBe('string')

    const socket = new WebSocket(String(material.endpoint))
    await waitOpen(socket)
    socket.send(JSON.stringify({
      type: 'local_direct_hello',
      profile: 'local_direct',
      proof: material.proof,
      token: material.token,
    }))
    const ready = JSON.parse(await nextMessage(socket)) as Record<string, unknown>
    expect(ready.type).toBe('local_direct_ready')
    expect(server.status().connected).toBe(true)

    socket.send(JSON.stringify({
      type: 'local_identity',
      player_unique_id: '123',
      player_name: 'AI Farmer',
      is_ai_controlled: false,
      target_player_id: null,
      revision: 0,
    }))
    const control = JSON.parse(await nextMessage(socket)) as Record<string, unknown>
    expect(control.type).toBe('local_control')
    expect(control.target_player_id).toBe('123')
    expect(control.revision).toBe(1)
    expect(server.status().identity?.player_unique_id).toBe('123')

    const commandPromise = server.command('say', { message: 'hi' }, 2000)
    const command = JSON.parse(await nextMessage(socket)) as Record<string, unknown>
    expect(command.type).toBe('local_command')
    expect(command.wire_format).toBe('v2-json')
    expect(command.payload).toEqual({ capability: 'say', params: { message: 'hi' } })

    socket.send(JSON.stringify({
      type: 'local_command_result',
      command_id: command.command_id,
      state: 'SUCCEEDED',
      output: { success: true, observation: 'said hi' },
    }))
    const result = await commandPromise
    expect(result.state).toBe('SUCCEEDED')
    expect(result.output).toEqual({ success: true, observation: 'said hi' })

    socket.close()
    await server.stop()
  })

  it('rejects a hello without the instance token', async () => {
    const dir = tempDir()
    const rendezvousPath = join(dir, 'rendezvous.json')
    const server = new StarCoPlayRouteServer({ rendezvousPath, capabilityEntries: entries })
    await server.start()
    const material = JSON.parse(readFileSync(rendezvousPath, 'utf8')) as Record<string, unknown>
    const socket = new WebSocket(String(material.endpoint))
    await waitOpen(socket)
    const closed = new Promise<void>((resolve) => socket.once('close', () => resolve()))
    socket.send(JSON.stringify({
      type: 'local_direct_hello',
      profile: 'local_direct',
      proof: material.proof,
      token: 'wrong-token',
    }))
    await closed
    expect(server.status().connected).toBe(false)
    await server.stop()
  })
})
