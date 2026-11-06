import { useState } from 'react'
import type { Model, Run } from '../api/schemas'
import { Badge, QueryCard, Row } from '../components/ui'
import type { Tone } from '../components/ui'
import { formatDateTime, formatMetric, metricEntries } from '../lib/format'
import { useSystemStatus } from '../query/useSystemStatus'

const runTone: Record<Run['status'], Tone> = {
  queued: 'warn',
  running: 'ok',
  stopping: 'warn',
  stopped: 'neutral',
  completed: 'ok',
  failed: 'bad',
}

function RunSummary({ run }: { run: Run }) {
  return (
    <dl className="divide-y divide-slate-100">
      <Row label="Run">#{run.id}</Row>
      <Row label="Status">
        <Badge tone={runTone[run.status]}>{run.status}</Badge>
      </Row>
      <Row label="Scenario">{run.scenario}</Row>
      <Row label="Engines">{run.engine_units.length}</Row>
      <Row label="Readings stored">{run.readings_stored}</Row>
      <Row label="Errors">{run.error_count}</Row>
      <Row label="Created">{formatDateTime(run.created_at)}</Row>
      <Row label="Ended">{formatDateTime(run.ended_at)}</Row>
      {run.failure_code && <Row label="Failure">{run.failure_message ?? run.failure_code}</Row>}
    </dl>
  )
}

function ModelDetails({ model }: { model: Model }) {
  const metrics = metricEntries(model.metrics)
  return (
    <>
      <dl className="divide-y divide-slate-100">
        <Row label="Version">{model.version}</Row>
        <Row label="Type">{model.model_type}</Row>
        <Row label="Dataset">{model.dataset ?? '—'}</Row>
        <Row label="Trained">{formatDateTime(model.trained_at)}</Row>
        <Row label="Activated">{formatDateTime(model.activated_at)}</Row>
        <Row label="RUL cap">{model.rul_cap}</Row>
        <Row label="Failure horizon">{model.failure_horizon}</Row>
      </dl>
      <h3 className="mt-4 mb-1 font-semibold">Metrics</h3>
      {metrics.length === 0 ? (
        <p className="text-slate-500">The backend returned no numeric metrics for this model.</p>
      ) : (
        <dl className="divide-y divide-slate-100">
          {metrics.map((m) => (
            <Row key={m.key} label={m.key}>
              {formatMetric(m.value)}
            </Row>
          ))}
        </dl>
      )}
    </>
  )
}

export default function SystemPage() {
  const [autoRefresh, setAutoRefresh] = useState(true)
  const { health, ready, simulation, model, refresh, isRefreshing } = useSystemStatus({ autoRefresh })

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">System status</h1>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
            Auto-refresh
          </label>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={isRefreshing}
            className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {isRefreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <QueryCard title="API health" query={health}>
          {() => (
            <p>
              <Badge tone="ok">API healthy</Badge>
            </p>
          )}
        </QueryCard>

        <QueryCard title="Readiness" query={ready}>
          {(data) => (
            <>
              <p className="mb-2">
                <Badge tone={data.status === 'ready' ? 'ok' : 'warn'}>{data.status === 'ready' ? 'Ready' : 'Not ready'}</Badge>
              </p>
              <dl className="divide-y divide-slate-100">
                {Object.entries(data.checks).map(([name, value]) => (
                  <Row key={name} label={name}>
                    <Badge tone={value === 'ok' ? 'ok' : 'bad'}>{value}</Badge>
                  </Row>
                ))}
              </dl>
            </>
          )}
        </QueryCard>

        <QueryCard title="Simulation" query={simulation}>
          {(data) => (
            <>
              <dl className="divide-y divide-slate-100">
                <Row label="Worker">
                  <Badge tone={data.worker_online ? 'ok' : 'neutral'}>{data.worker_online ? 'Online' : 'Offline'}</Badge>
                </Row>
                <Row label="Workers online">{data.workers_online}</Row>
              </dl>
              <h3 className="mt-4 mb-1 font-semibold">Active run</h3>
              {data.active_run ? <RunSummary run={data.active_run} /> : <p className="text-slate-500">No active run.</p>}
              <h3 className="mt-4 mb-1 font-semibold">Last run</h3>
              {data.last_run ? <RunSummary run={data.last_run} /> : <p className="text-slate-500">No runs yet.</p>}
            </>
          )}
        </QueryCard>

        <QueryCard title="Active model" query={model}>
          {(data) => (data ? <ModelDetails model={data} /> : <p className="text-slate-500">No active model is registered.</p>)}
        </QueryCard>
      </div>
    </div>
  )
}
