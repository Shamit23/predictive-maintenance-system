export function formatDateTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
}

export interface MetricEntry {
  key: string
  value: number
}

/**
 * Metrics are free-form on the backend (`dict`) and nested to any depth, e.g. `validation_all_cycles.xgboost.mae`.
 * Show only what is really there: finite numbers, with every nesting level flattened to a dotted key. Arrays (such
 * as confusion matrices), booleans, strings and null are skipped and never traversed. Nothing is invented.
 */
export function metricEntries(metrics: Record<string, unknown>): MetricEntry[] {
  const out: MetricEntry[] = []
  const walk = (node: Record<string, unknown>, prefix: string) => {
    for (const [key, value] of Object.entries(node)) {
      const path = prefix ? `${prefix}.${key}` : key
      if (typeof value === 'number') {
        if (Number.isFinite(value)) out.push({ key: path, value })
      } else if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
        walk(value as Record<string, unknown>, path)
      }
    }
  }
  walk(metrics, '')
  return out
}

export interface MetricGroup {
  group: string
  entries: { name: string; value: number }[]
}

/** Group for metrics that cannot be split into a group and a name, so a top-level scalar is never dropped. */
const GENERAL_GROUP = 'General'

/**
 * Group flat dotted metrics by their first key segment: `validation_all_cycles.xgboost.mae` becomes group
 * `validation_all_cycles`, name `xgboost.mae`. Groups and the entries inside them keep their input order, and every
 * input entry appears exactly once in the output, so no metric is ever lost.
 *
 * A key is only split when it has text on both sides of its first dot. Dotless keys (`rmse`) and keys with a leading
 * or trailing dot (`.a`, `a.`) go to `General` under their full key, so no label is ever blank. Entries are not
 * de-duplicated: colliding keys (e.g. `{"a.b": 1, a: {b: 2}}`) both survive, which is why callers must not use a
 * metric name as a unique key.
 */
export function groupMetrics(entries: MetricEntry[]): MetricGroup[] {
  const groups = new Map<string, MetricGroup>()
  for (const { key, value } of entries) {
    const dot = key.indexOf('.')
    const grouped = dot > 0 && dot < key.length - 1
    const groupName = grouped ? key.slice(0, dot) : GENERAL_GROUP
    let group = groups.get(groupName)
    if (!group) {
      group = { group: groupName, entries: [] }
      groups.set(groupName, group)
    }
    group.entries.push({ name: grouped ? key.slice(dot + 1) : key, value })
  }
  return [...groups.values()]
}

export function formatMetric(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(3)
}

/** Predicted RUL in cycles, one decimal place. */
export function formatRul(value: number): string {
  return value.toFixed(1)
}

/**
 * Human wording for an alert's `resolution_reason`. The backend documents two reasons: `recovered` (the engine returned
 * to normal) and `escalated` (an acknowledged alert worsened and was replaced by a new, more severe one). Anything
 * else is shown as received rather than guessed at.
 */
export function describeResolution(reason: string | null): string | null {
  if (reason === null) return null
  if (reason === 'recovered') return 'Recovered'
  if (reason === 'escalated') return 'Escalated (replaced by a new alert)'
  return reason
}

/** `1 engine`, `2 engines`: the singular form for exactly one, otherwise the plural (default: singular + "s"). */
export function pluralize(count: number, singular: string, plural: string = `${singular}s`): string {
  return count === 1 ? singular : plural
}
