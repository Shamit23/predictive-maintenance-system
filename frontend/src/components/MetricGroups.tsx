import { formatMetric, groupMetrics, metricEntries } from '../lib/format'
import type { MetricGroup } from '../lib/format'
import { Row } from './ui'

/**
 * One metric group as a native disclosure: keyboard operable and announced by assistive tech without any state of our
 * own. `open` only sets the initial state, so a user's toggle survives refetches (the key is the stable group name).
 * The summary stays a normal list-item box so the browser's disclosure marker remains visible.
 */
function MetricGroupSection({ group, defaultOpen }: { group: MetricGroup; defaultOpen: boolean }) {
  const count = group.entries.length
  return (
    <details open={defaultOpen}>
      <summary className="cursor-pointer rounded-md py-2.5 font-medium hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600">
        <span className="break-all">{group.group}</span>{' '}
        <span className="text-sm font-normal text-slate-500">
          ({count} {count === 1 ? 'metric' : 'metrics'})
        </span>
      </summary>
      <dl className="divide-y divide-slate-100 pb-2 pl-5">
        {/* Index keys: names can repeat inside a group (colliding nested/dotted keys), and rows are stateless. */}
        {group.entries.map((m, index) => (
          <Row key={index} label={m.name}>
            {formatMetric(m.value)}
          </Row>
        ))}
      </dl>
    </details>
  )
}

/**
 * A model's free-form `metrics` dict, grouped by top-level key and collapsible. The first group starts open. Shows
 * only numeric values actually returned (see `metricEntries`); with none, the empty message.
 */
export function MetricGroups({ metrics }: { metrics: Record<string, unknown> }) {
  const groups = groupMetrics(metricEntries(metrics))
  if (groups.length === 0) {
    return <p className="text-slate-500">The backend returned no numeric metrics for this model.</p>
  }
  return (
    <div className="divide-y divide-slate-100">
      {groups.map((g, index) => (
        <MetricGroupSection key={g.group} group={g} defaultOpen={index === 0} />
      ))}
    </div>
  )
}
