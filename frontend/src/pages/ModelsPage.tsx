import { useSearchParams } from 'react-router'
import type { Model } from '../api/schemas'
import { MetricGroups } from '../components/MetricGroups'
import { EmptyState, PageError, PageLoading } from '../components/PageState'
import { PageHeader } from '../components/PageHeader'
import { Pagination } from '../components/Pagination'
import { ResultsStatus } from '../components/ResultsStatus'
import { ModelStatusBadge } from '../components/StatusBadges'
import { ErrorNotice, Card, Row } from '../components/ui'
import { formatDateTime, metricEntries } from '../lib/format'
import { offsetForPage, parsePage } from '../lib/params'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { MODELS_PAGE_SIZE, useModels } from '../query/useModels'

function ModelCard({ model }: { model: Model }) {
  const count = metricEntries(model.metrics).length
  return (
    <Card title={model.version}>
      <p className="mb-2">
        <ModelStatusBadge status={model.status} />
      </p>
      <dl className="divide-y divide-slate-100">
        <Row label="Type">{model.model_type}</Row>
        <Row label="Dataset">{model.dataset ?? '—'}</Row>
        <Row label="Trained">{formatDateTime(model.trained_at)}</Row>
        <Row label="Activated">{formatDateTime(model.activated_at)}</Row>
        <Row label="RUL cap">{model.rul_cap}</Row>
        <Row label="Failure horizon">{model.failure_horizon}</Row>
      </dl>
      {/* The active version's metrics start expanded; other versions stay collapsed behind one disclosure. */}
      <details open={model.status === 'active'} className="mt-4">
        <summary className="cursor-pointer rounded-md py-2.5 font-semibold hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600">
          Metrics{' '}
          <span className="text-sm font-normal text-slate-500">
            ({count} {count === 1 ? 'metric' : 'metrics'})
          </span>
        </summary>
        <MetricGroups metrics={model.metrics} />
      </details>
    </Card>
  )
}

export default function ModelsPage() {
  useDocumentTitle('Models')
  const [params, setParams] = useSearchParams()
  const page = parsePage(params.get('page'))
  const offset = offsetForPage(page, MODELS_PAGE_SIZE)
  const query = useModels(offset)

  const goToOffset = (next: number) => {
    const nextPage = Math.floor(next / MODELS_PAGE_SIZE) + 1
    setParams((previous) => {
      const updated = new URLSearchParams(previous)
      if (nextPage === 1) updated.delete('page')
      else updated.set('page', String(nextPage))
      return updated
    })
  }

  const data = query.data
  // True while the cards on screen still belong to the previous page (the new request is loading).
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
    body = query.isError ? <PageError error={query.error} onRetry={() => void query.refetch()} /> : <PageLoading what="models" />
  } else if (data.total === 0) {
    body = <EmptyState title="No model versions are registered." />
  } else {
    body = (
      <div>
        {data.items.length === 0 ? (
          <EmptyState title="This page is past the end of the list.">Use Previous to go back.</EmptyState>
        ) : (
          <div className="grid gap-4">
            {data.items.map((model) => (
              <ModelCard key={model.version} model={model} />
            ))}
          </div>
        )}
        {/* The pager describes the cards actually displayed (the response's own page), not the URL's pending offset. */}
        <Pagination total={data.total} limit={data.limit} offset={data.offset} noun="model version" onOffsetChange={goToOffset} />
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Models" onRefresh={() => void query.refetch()} isRefreshing={query.isFetching} />
      <ResultsStatus stale={stale} page={data} noun="model version" />
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
