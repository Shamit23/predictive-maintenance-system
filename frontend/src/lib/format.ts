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
 * Metrics are free-form on the backend (`dict`). Show only what is really there: finite numbers, with one level of
 * nesting flattened to dotted keys. Non-numeric values are ignored, nothing is invented.
 */
export function metricEntries(metrics: Record<string, unknown>): MetricEntry[] {
  const out: MetricEntry[] = []
  for (const [key, value] of Object.entries(metrics)) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      out.push({ key, value })
    } else if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
      for (const [inner, v] of Object.entries(value)) {
        if (typeof v === 'number' && Number.isFinite(v)) out.push({ key: `${key}.${inner}`, value: v })
      }
    }
  }
  return out
}

export function formatMetric(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(3)
}
