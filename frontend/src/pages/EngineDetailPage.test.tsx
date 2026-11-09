import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { formatDateTime } from '../lib/format'
import { deferred, engineDetail, engineSummary, errorEnvelope, page, predictionOut } from '../test/fixtures'
import { renderAppAt } from '../test/render'
import { server } from '../test/server'

type Prediction = ReturnType<typeof predictionOut>
type JsonBody = Parameters<typeof HttpResponse.json>[0]
type Risk = 'normal' | 'warning' | 'critical'

interface Recorded {
  url: URL
  method: string
  headers: Headers
}

interface ApiOptions {
  engine?: unknown
  /** The body of the predictions response. */
  history?: unknown
  detailStatus?: number
  historyStatus?: number
  detailBody?: unknown
  historyBody?: unknown
  detailFail?: (n: number) => boolean
  historyFail?: (n: number) => boolean
  holdDetail?: (n: number) => Promise<void> | undefined
  holdHistory?: (n: number) => Promise<void> | undefined
}

/** A backend stand-in for the two endpoints the detail page reads. Records every request. */
function engineApi({
  engine = engineDetail,
  history = page([], 0, 200, 0),
  detailStatus = 200,
  historyStatus = 200,
  detailBody,
  historyBody,
  detailFail = () => false,
  historyFail = () => false,
  holdDetail,
  holdHistory,
}: ApiOptions = {}) {
  const detailRequests: Recorded[] = []
  const historyRequests: Recorded[] = []
  const boom = () => HttpResponse.json(errorEnvelope('internal_error', 'Server exploded'), { status: 500 })
  const handlers = [
    http.get('*/api/v1/engines/:id', async ({ request }) => {
      detailRequests.push({ url: new URL(request.url), method: request.method, headers: request.headers })
      await holdDetail?.(detailRequests.length)
      if (detailFail(detailRequests.length)) return boom()
      return HttpResponse.json((detailBody ?? engine) as JsonBody, { status: detailStatus })
    }),
    http.get('*/api/v1/engines/:id/predictions', async ({ request }) => {
      historyRequests.push({ url: new URL(request.url), method: request.method, headers: request.headers })
      await holdHistory?.(historyRequests.length)
      if (historyFail(historyRequests.length)) return boom()
      return HttpResponse.json((historyBody ?? history) as JsonBody, { status: historyStatus })
    }),
  ]
  return { handlers, detailRequests, historyRequests }
}

const notFound = (what = 'Engine 999') => errorEnvelope('not_found', `${what} not found.`)

/** `count` predictions on consecutive cycles, newest first (the order the API returns with order=desc). */
function newestFirst(count: number, { firstCycle = 1, version = 'xgb-test-1', risk = (): Risk => 'normal' }: { firstCycle?: number; version?: string; risk?: (i: number) => Risk } = {}): Prediction[] {
  const items = Array.from({ length: count }, (_, i) => predictionOut(firstCycle + i, Math.max(0, 120 - i * 0.5), risk(i), version))
  return items.reverse()
}
const historyOf = (items: Prediction[], total = items.length) => page(items, total, 200, 0)

const overview = () => within(screen.getByRole('region', { name: 'Overview' }))
const historyCard = () => within(screen.getByRole('region', { name: 'RUL history' }))
/** Waits (retrying the region lookup as well) until the history card contains this text. */
const inHistory = (text: string | RegExp) => waitFor(() => expect(historyCard().getByText(text)).toBeInTheDocument())
const valueOf = (label: string) => overview().getByText(label).nextElementSibling as HTMLElement
const tableRows = () => within(screen.getByRole('region', { name: 'Prediction history' })).getAllByRole('row').slice(1)
const markerTitles = () =>
  [...(screen.getByRole('img') as unknown as SVGSVGElement).querySelectorAll('title')].map((t) => t.textContent ?? '').filter((text) => text.startsWith('Cycle '))
const buttons = () => [...document.querySelectorAll('main button')].map((b) => b.textContent)
const insideBusy = (element: HTMLElement) => element.closest('[aria-busy="true"]')
const settle = () => new Promise((resolve) => setTimeout(resolve, 30))

describe('EngineDetailPage: engine ids are validated before any request', () => {
  it.each(['abc', '0', '-1', '1.5', '24abc', '1234567890', '00', '%20', '2e3'])('rejects the id %j without requesting anything', async (id) => {
    const api = engineApi()
    server.use(...api.handlers)
    renderAppAt(`/engines/${id}`)
    expect(await screen.findByRole('heading', { level: 1, name: 'Invalid engine id' })).toBeInTheDocument()
    expect(screen.getByText(/No request was made\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to engines' })).toHaveAttribute('href', '/engines')
    await settle()
    expect(api.detailRequests).toHaveLength(0)
    expect(api.historyRequests).toHaveLength(0)
    expect(document.title).toBe('Invalid engine id | Predictive Maintenance')
  })

  it.each(['1', '24', '91100', '999999999'])('accepts the id %s and requests exactly that engine', async (id) => {
    const api = engineApi({ engine: { ...engineDetail, id: Number(id) } })
    server.use(...api.handlers)
    renderAppAt(`/engines/${id}`)
    expect(await screen.findByRole('region', { name: 'Overview' })).toBeInTheDocument()
    expect(api.detailRequests[0]?.url.pathname).toBe(`/api/v1/engines/${id}`)
    expect(api.historyRequests[0]?.url.pathname).toBe(`/api/v1/engines/${id}/predictions`)
  })
})

describe('EngineDetailPage: requests', () => {
  it('reads the engine and its latest 200 predictions (newest first) with credential-free GETs only', async () => {
    const api = engineApi({ history: historyOf(newestFirst(3)) })
    server.use(...api.handlers)
    renderAppAt('/engines/24')
    await screen.findByRole('region', { name: 'RUL history' })
    expect(api.detailRequests).toHaveLength(1)
    expect(api.historyRequests).toHaveLength(1)
    expect(api.detailRequests[0]?.url.search).toBe('')
    expect(api.historyRequests[0]?.url.search).toBe('?order=desc&limit=200&offset=0')
    for (const request of [...api.detailRequests, ...api.historyRequests]) {
      expect(request.method).toBe('GET')
      for (const name of ['x-api-key', 'x-control-key', 'authorization', 'cookie']) expect(request.headers.has(name)).toBe(false)
    }
  })

  it('offers no mutation controls: the only button is Refresh, and there are no forms or inputs', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(3)) }).handlers)
    renderAppAt('/engines/24')
    await screen.findByRole('region', { name: 'RUL history' })
    expect(buttons()).toEqual(['Refresh'])
    expect(document.querySelectorAll('main form, main input, main select, main textarea')).toHaveLength(0)
  })
})

describe('EngineDetailPage: loading and the overview', () => {
  it('shows the title and a loading message outside any busy region, then the engine', async () => {
    const gate = deferred()
    server.use(...engineApi({ history: historyOf(newestFirst(3)), holdDetail: () => gate.promise }).handlers)
    renderAppAt('/engines/24')
    expect(screen.getByRole('heading', { level: 1, name: 'Engine 24' })).toBeInTheDocument()
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent('Loading engine…')
    expect(insideBusy(status)).toBeNull()
    expect(document.title).toBe('Engine 24 | Predictive Maintenance')
    await act(async () => gate.release())
    expect(await screen.findByRole('region', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.queryByText('Loading engine…')).not.toBeInTheDocument()
  })

  it('shows the engine\'s metadata and latest operational details', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(3)) }).handlers)
    renderAppAt('/engines/24')
    await screen.findByRole('region', { name: 'Overview' })
    expect(valueOf('Data origin')).toHaveTextContent('Simulated')
    expect(valueOf('Risk (latest prediction)')).toHaveTextContent('Warning')
    expect(valueOf('Unresolved alert')).toHaveTextContent('WarningOpen')
    expect(valueOf('Last cycle')).toHaveTextContent('100')
    expect(valueOf('Last reading')).toHaveTextContent(formatDateTime('2026-01-01T10:00:00Z'))
    expect(valueOf('Readings stored')).toHaveTextContent('100')
    expect(valueOf('Predictions stored')).toHaveTextContent('100')
    expect(valueOf('First seen')).toHaveTextContent(formatDateTime('2026-01-01T09:00:00Z'))
    expect(valueOf('Predicted RUL (model estimate)')).toHaveTextContent('45.5 cycles')
    expect(valueOf('At cycle')).toHaveTextContent('100')
    expect(valueOf('Model version')).toHaveTextContent('xgb-test-1')
    expect(valueOf('Failure score (uncalibrated)')).toHaveTextContent('0.500')
    expect(valueOf('Recorded')).toHaveTextContent(formatDateTime('2026-01-01T10:00:00Z'))
  })

  it('labels ordinary ingestion as such, never as "real"', async () => {
    server.use(...engineApi({ engine: { ...engineDetail, origin: 'api' }, history: historyOf(newestFirst(2)) }).handlers)
    renderAppAt('/engines/24')
    await screen.findByRole('region', { name: 'Overview' })
    expect(valueOf('Data origin')).toHaveTextContent('Ordinary ingestion')
    expect(screen.queryByText(/real/i)).not.toBeInTheDocument()
  })

  it('shows an engine with no prediction and no alert plainly', async () => {
    const bare = { ...engineDetail, latest_prediction: null, risk_category: null, alert_status: null, alert_severity: null, last_reading_at: null, last_cycle: 0, prediction_count: 0 }
    server.use(...engineApi({ engine: bare }).handlers)
    renderAppAt('/engines/24')
    await screen.findByRole('region', { name: 'Overview' })
    expect(valueOf('Risk (latest prediction)')).toHaveTextContent('No prediction')
    expect(valueOf('Unresolved alert')).toHaveTextContent('None')
    expect(valueOf('Last reading')).toHaveTextContent('—')
    expect(overview().getByText('No prediction has been stored for this engine yet.')).toBeInTheDocument()
  })

  it('links to this engine\'s alerts', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(2)) }).handlers)
    renderAppAt('/engines/24')
    await screen.findByRole('region', { name: 'Overview' })
    expect(screen.getByRole('link', { name: 'View alerts for this engine' })).toHaveAttribute('href', '/alerts?engine_id=24&status=all')
  })
})

describe('EngineDetailPage: not found and errors', () => {
  it('shows "not found" for an unknown engine, with a way back and no retry or history', async () => {
    const api = engineApi({ detailStatus: 404, detailBody: notFound(), historyStatus: 404, historyBody: notFound() })
    server.use(...api.handlers)
    renderAppAt('/engines/999')
    expect(await screen.findByText('Engine 999 was not found.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Engine 999' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to engines' })).toHaveAttribute('href', '/engines')
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'RUL history' })).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not call a 404 from a wrong route or an incompatible backend "not found"', async () => {
    const generic = { error: { code: 'http_error', message: 'Not Found', details: {} } }
    server.use(...engineApi({ detailStatus: 404, detailBody: generic, historyStatus: 404, historyBody: generic }).handlers)
    renderAppAt('/engines/24')
    expect(await screen.findByRole('alert')).toHaveTextContent('backend error')
    expect(screen.queryByText(/was not found/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('shows a backend error from the envelope and recovers with Try again', async () => {
    const api = engineApi({ history: historyOf(newestFirst(3)), detailFail: (n) => n <= 2 }) // the call and its one retry fail
    server.use(...api.handlers)
    renderAppAt('/engines/24')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('backend error')
    expect(alert).toHaveTextContent('Server exploded')
    expect(insideBusy(alert)).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('region', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows "API unreachable" for a gateway failure', async () => {
    server.use(...engineApi({ detailStatus: 502, detailBody: null, historyStatus: 502, historyBody: null }).handlers)
    renderAppAt('/engines/24')
    expect(await screen.findByRole('alert')).toHaveTextContent('API unreachable')
  })

  it('shows "Unexpected response" when the engine body breaks the contract', async () => {
    server.use(...engineApi({ engine: { id: 24 }, history: historyOf([]) }).handlers)
    renderAppAt('/engines/24')
    expect(await screen.findByRole('alert')).toHaveTextContent('Unexpected response')
  })

  it('keeps the overview when only the predictions request fails, and retries just that request', async () => {
    const api = engineApi({ historyFail: (n) => n <= 2 })
    server.use(...api.handlers)
    renderAppAt('/engines/24')
    await screen.findByRole('region', { name: 'Overview' })
    const alert = await historyCard().findByRole('alert')
    expect(alert).toHaveTextContent('Server exploded')
    expect(overview().getByText('Simulated')).toBeInTheDocument() // the overview is unaffected
    const detailBefore = api.detailRequests.length
    const historyBefore = api.historyRequests.length
    await userEvent.click(historyCard().getByRole('button', { name: 'Try again' }))
    await inHistory('No predictions have been stored for this engine yet.')
    expect(api.detailRequests).toHaveLength(detailBefore) // the engine itself was not re-requested
    expect(api.historyRequests).toHaveLength(historyBefore + 1)
  })
})

describe('EngineDetailPage: prediction history', () => {
  it('says when all stored predictions are shown, and shows each in the table and chart', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(40)) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 40 stored predictions.')
    expect(tableRows()).toHaveLength(40)
    expect(markerTitles()).toHaveLength(40)
    expect(historyCard().queryByText(/Earlier predictions are not shown/)).not.toBeInTheDocument()
  })

  it('uses the singular for one stored prediction', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(1)) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 1 stored prediction.')
    expect(tableRows()).toHaveLength(1)
    expect(markerTitles()).toHaveLength(1)
  })

  it('does not call exactly 200 predictions a limited view when 200 is the total', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(200)) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 200 stored predictions.')
    expect(tableRows()).toHaveLength(200)
    expect(markerTitles()).toHaveLength(200)
  })

  it('states clearly that the view is limited to the latest 200 when there are more', async () => {
    const latest200 = newestFirst(200, { firstCycle: 51 }) // cycles 51..250 of 250 stored
    server.use(...engineApi({ history: historyOf(latest200, 250) }).handlers)
    renderAppAt('/engines/24')
    await inHistory(/Showing the latest 200 of 250 predictions/)
    const note = historyCard().getByText(/Showing the latest 200 of 250 predictions/)
    expect(note).toHaveTextContent('Earlier predictions are not shown, so this is not the engine\'s complete history.')
    expect(note).toHaveTextContent('by cycle')
    expect(tableRows()).toHaveLength(200)
    expect(markerTitles()).toHaveLength(200)
    expect(historyCard().queryByText(/Showing all/)).not.toBeInTheDocument()
  })

  it('lists predictions newest cycle first with cycle, predicted RUL, risk, model version, score and time', async () => {
    const items = [predictionOut(12, 41.25, 'warning', 'xgb-b'), predictionOut(11, 70, 'normal', 'xgb-a')]
    server.use(...engineApi({ history: historyOf(items) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 2 stored predictions.')
    expect(within(screen.getByRole('region', { name: 'Prediction history' })).getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Cycle', 'Predicted RUL (cycles)', 'Risk', 'Model version', 'Failure score (uncalibrated)', 'Recorded',
    ])
    const cells = tableRows().map((row) => within(row).getAllByRole('cell').map((c) => c.textContent))
    expect(cells).toEqual([
      ['12', '41.3', 'Warning', 'xgb-b', '0.500', formatDateTime('2026-01-01T10:00:00Z')],
      ['11', '70.0', 'Normal', 'xgb-a', '0.500', formatDateTime('2026-01-01T10:00:00Z')],
    ])
  })

  it('keeps several predictions at one cycle from different model versions, in the table and the chart', async () => {
    const items = [
      { ...predictionOut(6, 43, 'warning', 'new'), id: 3 },
      { ...predictionOut(5, 44, 'warning', 'new'), id: 2 },
      { ...predictionOut(5, 50, 'normal', 'old'), id: 1 },
      { ...predictionOut(4, 55, 'normal', 'old'), id: 0 },
    ]
    server.use(...engineApi({ history: historyOf(items) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 4 stored predictions.')
    expect(tableRows()).toHaveLength(4)
    const atFive = tableRows().filter((row) => within(row).getAllByRole('cell')[0]?.textContent === '5')
    expect(atFive.map((row) => within(row).getAllByRole('cell')[3]?.textContent).sort()).toEqual(['new', 'old'])
    expect(markerTitles()).toEqual([
      'Cycle 4: predicted RUL 55.0 cycles, Normal risk, model old',
      'Cycle 5: predicted RUL 50.0 cycles, Normal risk, model old',
      'Cycle 5: predicted RUL 44.0 cycles, Warning risk, model new',
      'Cycle 6: predicted RUL 43.0 cycles, Warning risk, model new',
    ])
    // The visible notes (the chart's accessible description repeats the first one in its own text, so match its wording).
    expect(historyCard().getByText(/more than one prediction \(from different model versions\)/)).toBeInTheDocument()
    expect(historyCard().getByText(/These predictions come from 2 model versions/)).toBeInTheDocument()
    expect(screen.getByRole('img')).toHaveAccessibleName(/1 cycle has more than one prediction; all are plotted/)
    const legend = historyCard().getByRole('heading', { name: 'Model versions' }).parentElement as HTMLElement
    expect(within(legend).getByText('old')).toBeInTheDocument()
    expect(within(legend).getByText('new')).toBeInTheDocument()
  })

  it('shows an empty state, without a chart or table, when no predictions are stored', async () => {
    server.use(...engineApi({ history: historyOf([]) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('No predictions have been stored for this engine yet.')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Prediction history' })).not.toBeInTheDocument()
  })

  it('describes the chart without implying predictions are actual remaining life', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(10)) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 10 stored predictions.')
    expect(screen.getByRole('img')).toHaveAccessibleName(/Predicted remaining useful life, 10 predictions from cycle 1 to cycle 10/)
    expect(document.body.textContent).not.toMatch(/ground[- ]truth|true RUL|actual RUL/i)
  })

  it('shows a loading message for the history while the overview is already there', async () => {
    const gate = deferred()
    server.use(...engineApi({ history: historyOf(newestFirst(3)), holdHistory: () => gate.promise }).handlers)
    renderAppAt('/engines/24')
    await screen.findByRole('region', { name: 'Overview' })
    const loading = await historyCard().findByRole('status')
    expect(loading).toHaveTextContent('Loading predictions…')
    expect(insideBusy(loading)).toBeNull()
    await act(async () => gate.release())
    await inHistory('Showing all 3 stored predictions.')
  })
})

describe('EngineDetailPage: refresh', () => {
  it('re-requests both the engine and its predictions only when Refresh is pressed', async () => {
    const api = engineApi({ history: historyOf(newestFirst(3)) })
    server.use(...api.handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 3 stored predictions.')
    expect([api.detailRequests.length, api.historyRequests.length]).toEqual([1, 1])
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect([api.detailRequests.length, api.historyRequests.length]).toEqual([2, 2]))
  })

  it('marks displayed data busy during a refresh, without a loading message replacing it', async () => {
    const gate = deferred()
    server.use(...engineApi({ history: historyOf(newestFirst(3)), holdDetail: (n) => (n === 2 ? gate.promise : undefined) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 3 stored predictions.')
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(valueOf('Data origin').closest('[aria-busy]')).toHaveAttribute('aria-busy', 'true')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    await act(async () => gate.release())
    await waitFor(() => expect(valueOf('Data origin').closest('[aria-busy]')).toHaveAttribute('aria-busy', 'false'))
  })

  it('keeps the engine on screen and says so when a refresh of it fails, outside the busy region', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(3)), detailFail: (n) => n === 2 || n === 3 }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 3 stored predictions.')
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Server exploded')
    expect(screen.getByText('Showing the last successful result.')).toBeInTheDocument()
    expect(insideBusy(alert)).toBeNull()
    expect(alert.closest('[aria-busy]')).toBeNull()
    expect(valueOf('Data origin')).toHaveTextContent('Simulated') // the previous details are still there
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled())
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
  })

  it('keeps the history on screen and says so when a refresh of it fails', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(3)), historyFail: (n) => n === 2 || n === 3 }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 3 stored predictions.')
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    const alert = await historyCard().findByRole('alert')
    expect(alert).toHaveTextContent('Server exploded')
    expect(historyCard().getByText('Showing the last successful result.')).toBeInTheDocument()
    expect(alert.closest('[aria-busy]')).toBeNull()
    expect(tableRows()).toHaveLength(3)
    expect(markerTitles()).toHaveLength(3)
  })
})

describe('EngineDetailPage: back links', () => {
  const open = (state?: unknown) => {
    server.use(...engineApi({ history: historyOf(newestFirst(2)) }).handlers)
    renderAppAt({ pathname: '/engines/24', state })
  }

  it('returns to the plain Engines list when there is no origin', async () => {
    open()
    await screen.findByRole('region', { name: 'Overview' })
    expect(screen.getByRole('link', { name: 'Back to engines' })).toHaveAttribute('href', '/engines')
    expect(screen.queryByRole('link', { name: 'Back to alerts' })).not.toBeInTheDocument()
  })

  it('restores the Engines list exactly as it was (filters and page)', async () => {
    open({ from: 'engines', search: '?risk=warning&alert=none&page=2' })
    await screen.findByRole('region', { name: 'Overview' })
    expect(screen.getByRole('link', { name: 'Back to engines' })).toHaveAttribute('href', '/engines?risk=warning&alert=none&page=2')
    expect(screen.queryByRole('link', { name: 'Back to alerts' })).not.toBeInTheDocument()
  })

  it('from the Alerts list, offers both the Engines list and the exact alerts view', async () => {
    open({ from: 'alerts', search: '?status=all&severity=critical' })
    await screen.findByRole('region', { name: 'Overview' })
    expect(screen.getByRole('link', { name: 'Back to engines' })).toHaveAttribute('href', '/engines')
    expect(screen.getByRole('link', { name: 'Back to alerts' })).toHaveAttribute('href', '/alerts?status=all&severity=critical')
  })

  it.each([
    ['an unknown origin', { from: 'evil', search: '?x=1' }],
    ['a search that is not a query string', { from: 'engines', search: 'javascript:alert(1)' }],
    ['a non-object', 'engines'],
  ])('ignores untrusted history state (%s)', async (_label, state) => {
    open(state)
    await screen.findByRole('region', { name: 'Overview' })
    expect(screen.getByRole('link', { name: 'Back to engines' })).toHaveAttribute('href', '/engines')
    expect(screen.queryByRole('link', { name: 'Back to alerts' })).not.toBeInTheDocument()
  })

  it('marks the Engines navigation link as current on a detail page', async () => {
    open()
    await screen.findByRole('region', { name: 'Overview' })
    const nav = within(screen.getByRole('navigation', { name: 'Main' }))
    expect(nav.getByRole('link', { name: 'Engines' })).toHaveAttribute('aria-current', 'page')
    expect(nav.getByRole('link', { name: 'System' })).not.toHaveAttribute('aria-current')
  })
})

describe('EngineDetailPage: structure', () => {
  it('has a level-1 heading, level-2 sections and titled, labelled regions', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(3)) }).handlers)
    renderAppAt('/engines/24')
    await inHistory('Showing all 3 stored predictions.')
    expect(screen.getAllByRole('heading', { level: 1 }).map((h) => h.textContent)).toEqual(['Engine 24'])
    expect(screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)).toEqual(['Overview', 'RUL history'])
    expect(screen.getByRole('table')).toHaveAccessibleName('Predictions for engine 24, latest cycle first')
  })

  it('announces the page after navigating in from the list (route announcer)', async () => {
    server.use(...engineApi({ history: historyOf(newestFirst(2)) }).handlers)
    server.use(http.get('*/api/v1/engines', () => HttpResponse.json(page([engineSummary(24)], 1, 25, 0))))
    renderAppAt('/engines')
    await userEvent.click(await screen.findByRole('link', { name: 'View details for engine 24' }))
    expect(await screen.findByRole('region', { name: 'Overview' })).toBeInTheDocument()
    expect(screen.getByTestId('route-announcer')).toHaveTextContent('Navigated to Engine 24')
    expect(screen.getByRole('main')).toHaveFocus()
  })
})
