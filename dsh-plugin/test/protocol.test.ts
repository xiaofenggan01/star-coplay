import { describe, expect, it } from 'vitest'
import {
  buildLocalCommand,
  fenceOf,
  isTerminalState,
  type LocalDirectFence,
} from '../src/route/protocol.js'

const material: LocalDirectFence = {
  context_ref: 'dsh-local',
  principal_ref: 'dsh-local',
  runtime_ref: 'dsh-local',
  game_session_ref: 'dsh-local',
  game_instance_nonce: 'nonce',
  generation: 1,
  binding_ref: 'dsh-local',
  binding_version: 1,
  lease_epoch: 1,
  capability_digest: 'sha256:abc',
}

describe('local command frame', () => {
  it('builds the v2-json wire format', () => {
    const frame = buildLocalCommand(
      material, 'say', { message: 'hi' }, 'cmd_1', 30000, 1000000,
    )
    expect(frame.type).toBe('local_command')
    expect(frame.wire_format).toBe('v2-json')
    expect(frame.payload).toEqual({ capability: 'say', params: { message: 'hi' } })
    expect(frame.deadline_at).toBe(1030)
    expect(frame.fence).toEqual(fenceOf(material))
  })

  it('rejects invalid input', () => {
    expect(() => buildLocalCommand(material, '', {}, 'cmd', 1000)).toThrow()
    expect(() => buildLocalCommand(material, 'say', {}, '', 1000)).toThrow()
    expect(() => buildLocalCommand(material, 'say', {}, 'cmd', 0)).toThrow()
  })

  it('recognises terminal states', () => {
    expect(isTerminalState('SUCCEEDED')).toBe(true)
    expect(isTerminalState('FAILED')).toBe(true)
    expect(isTerminalState('ACCEPTED')).toBe(false)
    expect(isTerminalState('RUNNING')).toBe(false)
  })
})
