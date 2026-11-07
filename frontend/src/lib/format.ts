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

export function formatMetric(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(3)
}
