import { describe, expect, it } from 'vitest'
import {
  canonicalEntries,
  computeCapabilitySnapshotDigest,
  type CapabilityEntry,
} from '../src/route/digest.js'

const observe: CapabilityEntry = {
  source_surface: 'v2-compat',
  capability_id: 'observe_current_view',
  capability_contract_version: '2.4.5',
}

const say: CapabilityEntry = {
  source_surface: 'v2-compat',
  capability_id: 'say',
  capability_contract_version: '2.4.5',
}

describe('capability digest', () => {
  it('is independent of input order and duplicates', () => {
    const a = computeCapabilitySnapshotDigest([say, observe, observe])
    const b = computeCapabilitySnapshotDigest([observe, say])
    expect(a).toBe(b)
    expect(a).toMatch(/^sha256:[0-9a-f]{64}$/)
  })

  it('sorts entries canonically', () => {
    const entries = canonicalEntries([say, observe])
    expect(entries.map((entry) => entry.capability_id)).toEqual([
      'observe_current_view',
      'say',
    ])
  })

  it('rejects an empty snapshot', () => {
    expect(() => canonicalEntries([])).toThrow('capability snapshot is empty')
  })
})
