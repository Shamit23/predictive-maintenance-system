import { useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation, useSearchParams } from 'react-router'
import type { EngineSummary } from '../api/schemas'
import { EngineId } from '../components/EngineId'
import { FilterSelect } from '../components/FilterSelect'
import { PageHeader } from '../components/PageHeader'
import { EmptyState, PageError, PageLoading } from '../components/PageState'
import { Pagination } from '../components/Pagination'
import { ResultsStatus } from '../components/ResultsStatus'
import { AlertSeverityBadge, AlertStatusBadge, OriginBadge, RiskBadge } from '../components/StatusBadges'
import { Td, TableWrap, Th } from '../components/Table'
import { ErrorNotice } from '../components/ui'
import { formatDateTime, formatRul } from '../lib/format'
import { offsetForPage, pageParamForOffset, parseDigits, parseEnum, parsePage, withParams } from '../lib/params'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { ENGINES_PAGE_SIZE, useEngines } from '../query/useEngines'

// URL: ?q=<digits, at most 20>  &risk=normal|warning|critical  &alert=open|acknowledged|none  &page=<n>
// Invalid values are ignored, so a hand-edited URL never produces a request the backend would reject.
const RISK_VALUES = ['normal', 'warning', 'critical'] as const
const ALERT_VALUES = ['open', 'acknowledged', 'none'] as const
const SEARCH_MAX_LENGTH = 20 // the backend's limit for `search`

const riskOptions = [
  { value: '', label: 'All risk levels' },
  { value: 'normal', label: 'Normal' },
  { value: 'warning', label: 'Warning' },
  { value: 'critical', label: 'Critical' },
]
const alertOptions = [
  { value: '', label: 'Any alert state' },
  { value: 'open', label: 'Open alert' },
  { value: 'acknowledged', label: 'Acknowledged alert' },
  { value: 'none', label: 'No unresolved alert' },
]

/**
 * Substring search on the engine id. The page keys it by the URL value plus a reset counter, so Back and "Clear
 * filters" both discard any typed or invalid draft, even when the URL value itself did not change.
 */
function SearchForm({ initial, onSearch }: { initial: string; onSearch: (value: string | undefined) => void }) {
  const [draft, setDraft] = useState(initial)
  const [invalid, setInvalid] = useState(false)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = draft.trim()
    if (value === '') {
      setInvalid(false)
      onSearch(undefined)
    } else if (parseDigits(value, SEARCH_MAX_LENGTH) === undefined) {
      setInvalid(true) // nothing is requested for input the backend could not match
    } else {
      setInvalid(false)
      onSearch(value)
    }
  }

  return (
    <form role="search" noValidate onSubmit={submit} className="flex flex-col gap-1">
      <label htmlFor="engine-search" className="text-sm font-medium text-slate-700">
        Engine id contains
      </label>
      <div className="flex gap-2">
        <input
          id="engine-search"
          type="text"
          inputMode="numeric"
          maxLength={SEARCH_MAX_LENGTH}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          aria-invalid={invalid}
          aria-describedby={invalid ? 'engine-search-error' : undefined}
          className="w-40 rounded-md border border-slate-300 bg-white px-2 py-1.5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        />
        <button
          type="submit"
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        >
          Search
        </button>
      </div>
      {invalid && (
        <p id="engine-search-error" role="alert" className="text-sm text-red-700">
          Enter digits only (at most {SEARCH_MAX_LENGTH}).
        </p>
      )}
    </form>
  )
}

function EngineRow({ engine, listSearch }: { engine: EngineSummary; listSearch: string }) {
  const prediction = engine.latest_prediction
  return (
    <tr>
      <Td>
        <EngineId id={engine.id} from={{ page: 'engines', search: listSearch }} />
      </Td>
      <Td>
        <OriginBadge origin={engine.origin} />
      </Td>
      <Td>{engine.last_cycle}</Td>
      <Td>{prediction ? formatRul(prediction.predicted_rul) : '—'}</Td>
      <Td>
        <RiskBadge risk={engine.risk_category} />
      </Td>
      <Td>
        {engine.alert_status && engine.alert_severity ? (
          <span className="flex flex-wrap gap-1">
            <AlertSeverityBadge severity={engine.alert_severity} />
            <AlertStatusBadge status={engine.alert_status} />
          </span>
        ) : (
          'None'
        )}
      </Td>
      <Td>{formatDateTime(engine.last_reading_at)}</Td>
    </tr>
  )
}

export default function EnginesPage() {
  useDocumentTitle('Engines')
  const [params, setParams] = useSearchParams()
  const { search: listSearch } = useLocation() // this list's own query string, so the detail page can return to it
  const [searchResets, setSearchResets] = useState(0)
  const search = parseDigits(params.get('q'), SEARCH_MAX_LENGTH)
  const risk = parseEnum(params.get('risk'), RISK_VALUES)
  const alertStatus = parseEnum(params.get('alert'), ALERT_VALUES)
  const page = parsePage(params.get('page'))
  const offset = offsetForPage(page, ENGINES_PAGE_SIZE)
  const hasFilters = search !== undefined || risk !== undefined || alertStatus !== undefined

  const query = useEngines({ search, risk, alertStatus, limit: ENGINES_PAGE_SIZE, offset })

  // Any filter change starts again on page 1.
  const change = (changes: Record<string, string | undefined>) => setParams((prev) => withParams(prev, { page: undefined, ...changes }))
  const clearFilters = () => {
    setSearchResets((count) => count + 1) // also empties the search box and its error, not just the URL filters
    change({ q: undefined, risk: undefined, alert: undefined })
  }

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
    body = query.isError ? <PageError error={query.error} onRetry={() => void query.refetch()} /> : <PageLoading what="engines" />
  } else if (data.total === 0) {
    body = hasFilters ? (
      <EmptyState title="No engines match these filters.">
        <button type="button" onClick={clearFilters} className="font-medium text-blue-700 underline">
          Clear filters
        </button>
      </EmptyState>
    ) : (
      <EmptyState title="No engines have been ingested yet.">Engines appear once readings are received.</EmptyState>
    )
  } else {
    body = (
      <div>
        {data.items.length === 0 ? (
          <EmptyState title="This page is past the end of the list.">Use Previous to go back.</EmptyState>
        ) : (
          <TableWrap label="Engines">
            <caption className="sr-only">Engines in engine id order</caption>
            <thead>
              <tr>
                <Th>Engine</Th>
                <Th>Data origin</Th>
                <Th>Last cycle</Th>
                <Th>Latest predicted RUL (cycles)</Th>
                <Th>Risk</Th>
                <Th>Unresolved alert</Th>
                <Th>Last reading</Th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((engine) => (
                <EngineRow key={engine.id} engine={engine} listSearch={listSearch} />
              ))}
            </tbody>
          </TableWrap>
        )}
        {/* The pager describes the rows actually displayed (the response's own page), not the URL's pending offset. */}
        <Pagination
          total={data.total}
          limit={data.limit}
          offset={data.offset}
          noun="engine"
          onOffsetChange={(next) => setParams((prev) => withParams(prev, { page: pageParamForOffset(next, ENGINES_PAGE_SIZE) }))}
        />
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Engines" onRefresh={() => void query.refetch()} isRefreshing={query.isFetching} />
      <p className="mb-4 text-sm text-slate-600">
        Engines are listed by engine id, exactly as the API returns them. This is not a risk ranking: filters search the
        whole fleet, but the order stays by id.
      </p>
      <div className="mb-4 flex flex-wrap items-end gap-4">
        <SearchForm key={`${search ?? ''}:${searchResets}`} initial={search ?? ''} onSearch={(value) => change({ q: value })} />
        <FilterSelect
          label="Risk"
          value={risk ?? ''}
          options={riskOptions}
          onChange={(value) => change({ risk: value === '' ? undefined : value })}
        />
        <FilterSelect
          label="Alert"
          value={alertStatus ?? ''}
          options={alertOptions}
          onChange={(value) => change({ alert: value === '' ? undefined : value })}
        />
      </div>
      <ResultsStatus stale={stale} page={data} noun="engine" />
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
