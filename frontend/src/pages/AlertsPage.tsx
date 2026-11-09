import { useLocation, useSearchParams } from 'react-router'
import type { Alert } from '../api/schemas'
import { EmptyState, PageError, PageLoading } from '../components/PageState'
import { EngineId } from '../components/EngineId'
import { FilterSelect } from '../components/FilterSelect'
import { PageHeader } from '../components/PageHeader'
import { Pagination } from '../components/Pagination'
import { ResultsStatus } from '../components/ResultsStatus'
import { AlertSeverityBadge, AlertStatusBadge } from '../components/StatusBadges'
import { Td, TableWrap, Th } from '../components/Table'
import { ErrorNotice } from '../components/ui'
import { describeResolution, formatDateTime, formatRul } from '../lib/format'
import { offsetForPage, pageParamForOffset, parseEnum, parseId, parsePage, withParams } from '../lib/params'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { ALERTS_PAGE_SIZE, useAlerts } from '../query/useAlerts'

// URL: ?status=unresolved|open|acknowledged|resolved|all  &severity=warning|critical  &engine_id=<id>  &page=<n>
// Status defaults to "unresolved" (the useful view) when absent or invalid; "all" is the explicit no-status filter.
const STATUS_VALUES = ['unresolved', 'open', 'acknowledged', 'resolved', 'all'] as const
const SEVERITY_VALUES = ['warning', 'critical'] as const
const DEFAULT_STATUS = 'unresolved'

const statusOptions = [
  { value: 'unresolved', label: 'Unresolved (open or acknowledged)' },
  { value: 'open', label: 'Open' },
  { value: 'acknowledged', label: 'Acknowledged' },
  { value: 'resolved', label: 'Resolved' },
  { value: 'all', label: 'All' },
]
const severityOptions = [
  { value: '', label: 'All severities' },
  { value: 'warning', label: 'Warning' },
  { value: 'critical', label: 'Critical' },
]

function AlertRow({ alert, listSearch }: { alert: Alert; listSearch: string }) {
  const resolution = describeResolution(alert.resolution_reason)
  return (
    <tr>
      <Td>
        <AlertSeverityBadge severity={alert.severity} />
      </Td>
      <Td>
        <AlertStatusBadge status={alert.status} />
      </Td>
      <Td>
        <EngineId id={alert.engine_id} from={{ page: 'alerts', search: listSearch }} />
      </Td>
      <Td>{formatRul(alert.predicted_rul)}</Td>
      <Td>{alert.model_version}</Td>
      <Td>{formatDateTime(alert.created_at)}</Td>
      <Td>{formatDateTime(alert.updated_at)}</Td>
      <Td>{formatDateTime(alert.acknowledged_at)}</Td>
      <Td>
        {alert.resolved_at ? formatDateTime(alert.resolved_at) : '—'}
        {resolution && <span className="block text-xs text-slate-600">{resolution}</span>}
      </Td>
    </tr>
  )
}

export default function AlertsPage() {
  useDocumentTitle('Alerts')
  const [params, setParams] = useSearchParams()
  const { search: listSearch } = useLocation() // this list's own query string, so the detail page can return to it
  const status = parseEnum(params.get('status'), STATUS_VALUES) ?? DEFAULT_STATUS
  const severity = parseEnum(params.get('severity'), SEVERITY_VALUES)
  const engineId = parseId(params.get('engine_id'))
  const page = parsePage(params.get('page'))
  const offset = offsetForPage(page, ALERTS_PAGE_SIZE)
  const isDefaultView = status === DEFAULT_STATUS && severity === undefined && engineId === undefined

  const query = useAlerts({
    status: status === 'all' ? undefined : status,
    severity,
    engineId,
    limit: ALERTS_PAGE_SIZE,
    offset,
  })

  // Any filter change starts again on page 1; the default status is represented by no parameter.
  const change = (changes: Record<string, string | undefined>) => setParams((prev) => withParams(prev, { page: undefined, ...changes }))
  const clearFilters = () => change({ status: undefined, severity: undefined, engine_id: undefined })

  const data = query.data
  // True while the rows on screen still belong to the previous filters or page (the new request is loading).
  const stale = query.isPlaceholderData
  // A failed background refresh keeps the previous data on screen. Its notice is rendered outside the busy region below.
  const refreshError =
    data !== undefined && query.isError ? (
      <div className="mb-4">
        <ErrorNotice error={query.error} />
        <p className="mt-1 text-sm text-slate-500">Showing the last successful result.</p>
      </div>
    ) : null
  let body
  if (data === undefined) {
    body = query.isError ? <PageError error={query.error} onRetry={() => void query.refetch()} /> : <PageLoading what="alerts" />
  } else if (data.total === 0) {
    body = isDefaultView ? (
      <EmptyState title="No unresolved alerts.">Alerts appear when an engine's predicted risk reaches warning or critical.</EmptyState>
    ) : (
      <EmptyState title="No alerts match these filters.">
        <button type="button" onClick={clearFilters} className="font-medium text-blue-700 underline">
          Clear filters
        </button>
      </EmptyState>
    )
  } else {
    body = (
      <div>
        {data.items.length === 0 ? (
          <EmptyState title="This page is past the end of the list.">Use Previous to go back.</EmptyState>
        ) : (
          <TableWrap label="Alerts">
            <caption className="sr-only">Alerts, newest first</caption>
            <thead>
              <tr>
                <Th>Severity</Th>
                <Th>Status</Th>
                <Th>Engine</Th>
                <Th>Predicted RUL (cycles)</Th>
                <Th>Model version</Th>
                <Th>Created</Th>
                <Th>Updated</Th>
                <Th>Acknowledged</Th>
                <Th>Resolved</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((alert) => (
                <AlertRow key={alert.id} alert={alert} listSearch={listSearch} />
              ))}
            </tbody>
          </TableWrap>
        )}
        {/* The pager describes the rows actually displayed (the response's own page), not the URL's pending offset. */}
        <Pagination
          total={data.total}
          limit={data.limit}
          offset={data.offset}
          noun="alert"
          onOffsetChange={(next) => setParams((prev) => withParams(prev, { page: pageParamForOffset(next, ALERTS_PAGE_SIZE) }))}
        />
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Alerts" onRefresh={() => void query.refetch()} isRefreshing={query.isFetching} />
      <p className="mb-4 text-sm text-slate-600">
        Newest first. This view is read-only: acknowledging alerts is not available in this interface.
      </p>
      <div className="mb-4 flex flex-wrap items-end gap-4">
        <FilterSelect
          label="Status"
          value={status}
          options={statusOptions}
          onChange={(value) => change({ status: value === DEFAULT_STATUS ? undefined : value })}
        />
        <FilterSelect
          label="Severity"
          value={severity ?? ''}
          options={severityOptions}
          onChange={(value) => change({ severity: value === '' ? undefined : value })}
        />
        {engineId !== undefined && (
          <p className="flex items-center gap-2 rounded-md bg-slate-100 px-3 py-1.5 text-sm">
            <span>Engine {engineId}</span>
            <button
              type="button"
              onClick={() => change({ engine_id: undefined })}
              aria-label="Clear engine filter"
              className="font-medium text-blue-700 underline"
            >
              Clear
            </button>
          </p>
        )}
      </div>
      <ResultsStatus stale={stale} page={data} noun="alert" />
      {refreshError}
      {data === undefined ? (
        body // the loading message and the initial error alert are never inside a busy region
      ) : (
        <div aria-busy={query.isFetching} className={stale ? 'opacity-50 transition-opacity' : undefined}>
          {body}
        </div>
      )}
    </div>
  )
}
