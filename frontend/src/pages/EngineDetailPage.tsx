import { Link, useLocation, useParams } from 'react-router'
import { ApiError } from '../api/errors'
import type { EngineDetail, Prediction } from '../api/schemas'
import { PageHeader } from '../components/PageHeader'
import { EmptyState, PageError, PageLoading } from '../components/PageState'
import { RulChart } from '../components/RulChart'
import { AlertSeverityBadge, AlertStatusBadge, OriginBadge, RiskBadge } from '../components/StatusBadges'
import { Td, TableWrap, Th } from '../components/Table'
import { Card, ErrorNotice, Row } from '../components/ui'
import { readEngineLinkState } from '../lib/engineLink'
import type { EngineLinkState } from '../lib/engineLink'
import { formatDateTime, formatRul, pluralize } from '../lib/format'
import { parseId } from '../lib/params'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { PREDICTION_HISTORY_LIMIT, useEngine, useEnginePredictions } from '../query/useEngine'

const textLink =
  'font-medium text-blue-700 underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600'

/** Back to the Engines list; if the visitor came from a list page, to that list exactly as they left it. */
function BackLinks({ origin }: { origin: EngineLinkState | undefined }) {
  const enginesSearch = origin?.from === 'engines' ? origin.search : ''
  return (
    <nav aria-label="Back links" className="mb-4 flex flex-wrap gap-4 text-sm">
      <Link to={{ pathname: '/engines', search: enginesSearch }} className={textLink}>
        <span aria-hidden="true">← </span>Back to engines
      </Link>
      {origin?.from === 'alerts' && (
        <Link to={{ pathname: '/alerts', search: origin.search }} className={textLink}>
          <span aria-hidden="true">← </span>Back to alerts
        </Link>
      )}
    </nav>
  )
}

/** A failed background refresh while earlier data is still on screen. Rendered outside any busy region. */
function RefreshNotice({ error }: { error: unknown }) {
  return (
    <div className="mb-4">
      <ErrorNotice error={error} />
      <p className="mt-1 text-sm text-slate-500">Showing the last successful result.</p>
    </div>
  )
}

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404 && error.code === 'not_found'
}

function Overview({ engine }: { engine: EngineDetail }) {
  const latest = engine.latest_prediction
  return (
    <>
      <dl className="divide-y divide-slate-100">
        <Row label="Data origin">
          <OriginBadge origin={engine.origin} />
        </Row>
        <Row label="Risk (latest prediction)">
          <RiskBadge risk={engine.risk_category} />
        </Row>
        <Row label="Unresolved alert">
          {engine.alert_status && engine.alert_severity ? (
            <span className="flex flex-wrap justify-end gap-1">
              <AlertSeverityBadge severity={engine.alert_severity} />
              <AlertStatusBadge status={engine.alert_status} />
            </span>
          ) : (
            'None'
          )}
        </Row>
        <Row label="Last cycle">{engine.last_cycle}</Row>
        <Row label="Last reading">{formatDateTime(engine.last_reading_at)}</Row>
        <Row label="Readings stored">{engine.reading_count}</Row>
        <Row label="Predictions stored">{engine.prediction_count}</Row>
        <Row label="First seen">{formatDateTime(engine.created_at)}</Row>
      </dl>
      <h3 className="mt-4 mb-1 font-semibold">Latest prediction</h3>
      {latest ? (
        <dl className="divide-y divide-slate-100">
          <Row label="Predicted RUL (model estimate)">{formatRul(latest.predicted_rul)} cycles</Row>
          <Row label="At cycle">{latest.cycle}</Row>
          <Row label="Model version">{latest.model_version}</Row>
          <Row label="Failure score (uncalibrated)">{latest.failure_score.toFixed(3)}</Row>
          <Row label="Recorded">{formatDateTime(latest.created_at)}</Row>
        </dl>
      ) : (
        <p className="text-slate-500">No prediction has been stored for this engine yet.</p>
      )}
      <p className="mt-4 text-sm">
        <Link to={`/alerts?engine_id=${engine.id}&status=all`} className={textLink}>
          View alerts for this engine
        </Link>
      </p>
    </>
  )
}

function PredictionTable({ items, engineId }: { items: Prediction[]; engineId: number }) {
  return (
    <details open className="mt-4">
      <summary className="cursor-pointer rounded-md py-2.5 font-semibold hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600">
        Prediction table{' '}
        <span className="text-sm font-normal text-slate-500">
          ({items.length} {pluralize(items.length, 'row')})
        </span>
      </summary>
      <TableWrap label="Prediction history">
        <caption className="sr-only">Predictions for engine {engineId}, latest cycle first</caption>
        <thead>
          <tr>
            <Th>Cycle</Th>
            <Th>Predicted RUL (cycles)</Th>
            <Th>Risk</Th>
            <Th>Model version</Th>
            <Th>Failure score (uncalibrated)</Th>
            <Th>Recorded</Th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.id}>
              <Td>{p.cycle}</Td>
              <Td>{formatRul(p.predicted_rul)}</Td>
              <Td>
                <RiskBadge risk={p.risk_category} />
              </Td>
              <Td>{p.model_version}</Td>
              <Td>{p.failure_score.toFixed(3)}</Td>
              <Td>{formatDateTime(p.created_at)}</Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </details>
  )
}

export default function EngineDetailPage() {
  const params = useParams()
  const location = useLocation()
  const engineId = parseId(params.engineId ?? null) // undefined for anything that is not a plain positive integer
  useDocumentTitle(engineId === undefined ? 'Invalid engine id' : `Engine ${engineId}`)
  const origin = readEngineLinkState(location.state)

  // With an invalid id both queries are disabled, so no request is made.
  const detail = useEngine(engineId)
  const history = useEnginePredictions(engineId)
  const refresh = () => Promise.all([detail.refetch(), history.refetch()])

  if (engineId === undefined) {
    return (
      <div>
        <BackLinks origin={undefined} />
        <h1 className="text-2xl font-bold">Invalid engine id</h1>
        <p className="mt-2 text-slate-600">
          An engine id is a positive whole number, such as <span className="font-medium">24</span>. No request was made.
        </p>
      </div>
    )
  }

  const engine = detail.data
  let content
  if (engine === undefined) {
    if (detail.isError) {
      content = isNotFound(detail.error) ? (
        <EmptyState title={`Engine ${engineId} was not found.`}>Check the id, or return to the list of engines.</EmptyState>
      ) : (
        <PageError error={detail.error} onRetry={() => void refresh()} />
      )
    } else {
      content = <PageLoading what="engine" />
    }
  } else {
    const data = history.data
    let historyBody
    if (data === undefined) {
      historyBody = history.isError ? (
        <PageError error={history.error} onRetry={() => void history.refetch()} />
      ) : (
        <PageLoading what="predictions" />
      )
    } else if (data.total === 0) {
      historyBody = <EmptyState title="No predictions have been stored for this engine yet." />
    } else {
      const limited = data.total > data.items.length
      historyBody = (
        <>
          {history.isError && <RefreshNotice error={history.error} />}
          <div aria-busy={history.isFetching}>
            <p className="mb-3 text-sm text-slate-700">
              {limited
                ? `Showing the latest ${data.items.length} of ${data.total} predictions, by cycle (at most ${PREDICTION_HISTORY_LIMIT} are requested). Earlier predictions are not shown, so this is not the engine's complete history.`
                : `Showing all ${data.total} stored ${pluralize(data.total, 'prediction')}.`}
            </p>
            <RulChart predictions={data.items} />
            <PredictionTable items={data.items} engineId={engineId} />
          </div>
        </>
      )
    }

    content = (
      <div className="grid gap-4">
        {detail.isError && <RefreshNotice error={detail.error} />}
        <Card title="Overview">
          <div aria-busy={detail.isFetching}>
            <Overview engine={engine} />
          </div>
        </Card>
        <Card title="RUL history">{historyBody}</Card>
      </div>
    )
  }

  return (
    <div>
      <BackLinks origin={origin} />
      <PageHeader title={`Engine ${engineId}`} onRefresh={() => void refresh()} isRefreshing={detail.isFetching || history.isFetching} />
      {content}
    </div>
  )
}
