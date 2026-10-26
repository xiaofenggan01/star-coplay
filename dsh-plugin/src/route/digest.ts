import { createHash } from 'node:crypto'

export interface CapabilityEntry {
  source_surface: string
  capability_id: string
  capability_contract_version: string
}

const SNAPSHOT_VERSION = 'starcoplay.capability-snapshot.v1'

function lineOf(entry: CapabilityEntry): string {
  return entry.source_surface + '\t' + entry.capability_id + '\t' +
    entry.capability_contract_version + '\n'
}

export function canonicalEntries(entries: CapabilityEntry[]): CapabilityEntry[] {
  const lines = new Set<string>()
  for (const entry of entries) {
    if (
      typeof entry?.source_surface !== 'string' ||
      typeof entry?.capability_id !== 'string' ||
      typeof entry?.capability_contract_version !== 'string' ||
      !entry.source_surface ||
      !entry.capability_id ||
      !entry.capability_contract_version
    ) {
      continue
    }
    lines.add(lineOf(entry))
  }
  if (lines.size === 0) throw new Error('capability snapshot is empty')
  return [...lines]
    .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    .map((line) => {
      const parts = line.replace(/\n$/, '').split('\t')
      return {
        source_surface: parts[0],
        capability_id: parts[1],
        capability_contract_version: parts[2],
      }
    })
}

export function computeCapabilitySnapshotDigest(entries: CapabilityEntry[]): string {
  const payload = SNAPSHOT_VERSION + '\n' + canonicalEntries(entries).map(lineOf).join('')
  return 'sha256:' + createHash('sha256').update(payload, 'utf8').digest('hex')
}
