import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { formatDateTime } from '../lib/format'
import { engineSummary, errorEnvelope, page, predictionOut } from '../test/fixtures'
import type { BackendOptions } from '../test/fixtures'
import { renderAppAt } from '../test/render'
import { server } from '../test/server'

type Engine = ReturnType<typeof engineSummary>

interface Recorded {
  url: URL
  method: string
  headers: Headers
}

/**
 * A backend stand-in for GET /api/v1/engines: substring id search, risk, alert_status (including "none"), limit and
 * offset, always ordered as given (the real endpoint orders by id). Records every request.
 */
function enginesBackend(all: Engine[], { fail = () => false, hold }: BackendOptions = {}) {
  const requests: Recorded[] = []
  const handler = http.get('*/api/v1/engines', async ({ request }) => {
    const url = new URL(request.url)
    requests.push({ url, method: request.method, headers: request.headers })
    const number = requests.length
    await hold?.(number)
    if (fail(number)) {
      return HttpResponse.json(errorEnvelope('internal_error', 'Server exploded'), { status: 500 })
    }
    const p = url.searchParams
    let rows = all
    if (p.get('search')) rows = rows.filter((e) => String(e.id).includes(p.get('search') as string))
    if (p.get('risk')) rows = rows.filter((e) => e.risk_category === p.get('risk'))
    const alert = p.get('alert_status')
    if (alert === 'none') rows = rows.filter((e) => e.alert_status === null)
    else if (alert) rows = rows.filter((e) => e.alert_status === alert)
    const limit = Number(p.get('limit'))
    const offset = Number(p.get('offset'))
    return HttpResponse.json(page(rows.slice(offset, offset + limit), rows.length, limit, offset))
  })
  return { handler, requests, last: () => requests[requests.length - 1]?.url as URL }
}

const noAlert = { alert_status: null, alert_severity: null }
const risks = ['normal', 'warning', 'critical'] as const

/** 60 engines with ids 1..60, cycling through risk levels, origins and alert states. */
const fleet: Engine[] = Array.from({ length: 60 }, (_, i) => {
  const id = i + 1
  const risk = risks[i % 3] as (typeof risks)[number]
  return engineSummary(id, {
    risk_category: risk,
    latest_prediction: predictionOut(id, 100 - id, risk),
    origin: i % 2 === 0 ? 'simulation' : 'api',
    ...(i % 3 === 0 ? noAlert : { alert_status: i % 3 === 1 ? 'open' : 'acknowledged', alert_severity: i % 3 === 1 ? 'warning' : 'critical' }),
  })
})

const table = () => within(screen.getByRole('region', { name: 'Engines' }))
const rows = () => table().getAllByRole('row').slice(1)
/** Waits (retrying the lookup too) until the table contains this text. */
const tableText = (text: string) => waitFor(() => expect(table().getByText(text)).toBeInTheDocument())
const tableTexts = (text: string) => waitFor(() => expect(table().getAllByText(text).length).toBeGreaterThan(0))
const ids = () => rows().map((row) => within(row).getAllByRole('cell')[0]?.textContent)

describe('EnginesPage: default view and data', () => {
  it('requests the first page of 25 with no filters', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines')
    expect(screen.getByRole('status')).toHaveTextContent('Loading engines…')
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()
    expect(backend.requests).toHaveLength(1)
    expect(backend.last().search).toBe('?limit=25&offset=0')
    expect(rows()).toHaveLength(25)
  })

  it('shows every column, including origin labels, risk, alert state and the last reading time', async () => {
    server.use(enginesBackend(fleet).handler)
    renderAppAt('/engines')
    await tableTexts('Simulated')
    expect(table().getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Engine', 'Data origin', 'Last cycle', 'Latest predicted RUL (cycles)', 'Risk', 'Unresolved alert', 'Last reading',
    ])
    const first = within(rows()[0] as HTMLElement) // engine 1: simulation, normal, no alert
    expect(first.getByText('1')).toBeInTheDocument()
    expect(first.getByText('Simulated')).toBeInTheDocument()
    expect(first.getByText('99.0')).toBeInTheDocument()
    expect(first.getByText('Normal')).toBeInTheDocument()
    expect(first.getByText('None')).toBeInTheDocument()
    expect(first.getByText(formatDateTime('2026-01-01T10:00:00Z'))).toBeInTheDocument()
    const second = within(rows()[1] as HTMLElement) // engine 2: ordinary ingestion, warning, open alert
    expect(second.getByText('Ordinary ingestion')).toBeInTheDocument()
    const cells = second.getAllByRole('cell')
    expect(cells[4]).toHaveTextContent('Warning') // Risk column
    expect(cells[5]).toHaveTextContent('WarningOpen') // alert severity badge, then alert status badge
    const third = within(rows()[2] as HTMLElement).getAllByRole('cell') // engine 3: critical, acknowledged alert
    expect(third[4]).toHaveTextContent('Critical')
    expect(third[5]).toHaveTextContent('CriticalAcknowledged')
  })

  it('never calls ordinary ingestion "real"', async () => {
    server.use(enginesBackend(fleet).handler)
    renderAppAt('/engines')
    await tableTexts('Ordinary ingestion')
    expect(screen.queryByText(/real/i)).not.toBeInTheDocument()
  })

  it('shows an engine without a prediction, alert or readings plainly', async () => {
    const bare = engineSummary(7, { latest_prediction: null, risk_category: null, last_reading_at: null, last_cycle: 0, ...noAlert })
    server.use(enginesBackend([bare]).handler)
    renderAppAt('/engines')
    await tableText('No prediction')
    const cells = within(rows()[0] as HTMLElement).getAllByRole('cell')
    expect(cells.map((c) => c.textContent)).toEqual(['7', 'Simulated', '0', '—', 'No prediction', 'None', '—'])
  })

  it('keeps the API order and says it is not a risk ranking', async () => {
    const unordered = [engineSummary(30), engineSummary(10), engineSummary(20)]
    server.use(enginesBackend(unordered).handler)
    renderAppAt('/engines')
    await tableText('30')
    expect(ids()).toEqual(['30', '10', '20']) // exactly as returned: no client-side sorting
    expect(screen.getByText(/listed by engine id, exactly as the api returns them/i)).toBeInTheDocument()
    expect(screen.getByText(/this is not a risk ranking/i)).toBeInTheDocument()
    expect(screen.getByRole('table')).toHaveAccessibleName('Engines in engine id order')
  })

  it('links each engine id to its detail page, and has no other links and no sortable headers', async () => {
    server.use(enginesBackend(fleet).handler)
    renderAppAt('/engines')
    await tableTexts('Simulated')
    const links = table().getAllByRole('link')
    expect(links).toHaveLength(25) // one per row
    expect(links.map((l) => l.getAttribute('href'))).toEqual(Array.from({ length: 25 }, (_, i) => `/engines/${i + 1}`))
    expect(links.map((l) => l.textContent)).toEqual(Array.from({ length: 25 }, (_, i) => String(i + 1)))
    expect(table().queryAllByRole('button')).toHaveLength(0)
  })
})

describe('EnginesPage: filters in the URL', () => {
  it('applies search, risk and alert filters from the URL with the backend parameter names', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines?q=2&risk=warning&alert=open')
    await waitFor(() => expect(backend.requests).toHaveLength(1))
    expect(backend.last().searchParams.get('search')).toBe('2')
    expect(backend.last().searchParams.get('risk')).toBe('warning')
    expect(backend.last().searchParams.get('alert_status')).toBe('open')
    expect(screen.getByLabelText('Engine id contains')).toHaveValue('2')
    expect(screen.getByLabelText('Risk')).toHaveValue('warning')
    expect(screen.getByLabelText('Alert')).toHaveValue('open')
  })

  it.each(['none', 'acknowledged'])('sends alert_status=%s', async (value) => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt(`/engines?alert=${value}`)
    await waitFor(() => expect(backend.requests).toHaveLength(1))
    expect(backend.last().searchParams.get('alert_status')).toBe(value)
  })

  it.each([
    'risk=severe', 'risk=CRITICAL', 'risk=', 'alert=maybe', 'alert=OPEN', 'q=abc', 'q=12a', 'q=-3', 'q=1.5', `q=${'9'.repeat(21)}`,
    'q=%25', 'page=abc', 'page=0', 'page=-1', 'limit=500&offset=900', 'order=desc',
  ])('ignores invalid URL parameters (%s) and sends a normal request', async (query) => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt(`/engines?${query}`)
    await screen.findByText('1–25 of 60 engines')
    expect(backend.requests).toHaveLength(1)
    expect(backend.last().search).toBe('?limit=25&offset=0')
  })

  it('submitting a search sets ?q=, requests it and returns to page 1', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines?page=2')
    await screen.findByText('26–50 of 60 engines')
    await userEvent.type(screen.getByLabelText('Engine id contains'), '4')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines\?q=4$/)
    await waitFor(() => expect(backend.last().searchParams.get('search')).toBe('4'))
    expect(backend.last().searchParams.get('offset')).toBe('0')
    expect(await screen.findByText('1–15 of 15 engines')).toBeInTheDocument() // ids containing "4" among 1..60
  })

  it('submits on Enter and trims whitespace', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines')
    await screen.findByText('1–25 of 60 engines')
    await userEvent.type(screen.getByLabelText('Engine id contains'), '  24 {Enter}')
    await waitFor(() => expect(backend.last().searchParams.get('search')).toBe('24'))
    expect(screen.getByTestId('location')).toHaveTextContent('/engines?q=24')
  })

  it('rejects non-numeric search input locally and sends no request', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines')
    await screen.findByText('1–25 of 60 engines')
    const input = screen.getByLabelText('Engine id contains')
    await userEvent.type(input, 'abc')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter digits only (at most 20).')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(backend.requests).toHaveLength(1)
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines$/)
    await userEvent.clear(input)
    await userEvent.type(input, '12')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    await waitFor(() => expect(backend.last().searchParams.get('search')).toBe('12'))
  })

  it('limits the search box to 20 characters, matching the backend', async () => {
    server.use(enginesBackend(fleet).handler)
    renderAppAt('/engines')
    await screen.findByText('1–25 of 60 engines')
    expect(screen.getByLabelText('Engine id contains')).toHaveAttribute('maxlength', '20')
    expect(screen.getByLabelText('Engine id contains')).toHaveAttribute('inputmode', 'numeric')
  })

  it('an empty submit clears the search', async () => {
    server.use(enginesBackend(fleet).handler)
    renderAppAt('/engines?q=24')
    await screen.findByText('1 of 1 engine')
    await userEvent.clear(screen.getByLabelText('Engine id contains'))
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines$/)
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()
  })

  it('changing risk or alert filters updates the URL and request and drops the page', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines?page=2')
    await screen.findByText('26–50 of 60 engines')
    await userEvent.selectOptions(screen.getByLabelText('Risk'), 'critical')
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines\?risk=critical$/)
    await waitFor(() => expect(backend.last().searchParams.get('risk')).toBe('critical'))
    expect(backend.last().searchParams.get('offset')).toBe('0')
    await userEvent.selectOptions(screen.getByLabelText('Alert'), 'none')
    expect(screen.getByTestId('location')).toHaveTextContent('/engines?risk=critical&alert=none')
    await waitFor(() => expect(backend.last().searchParams.get('alert_status')).toBe('none'))
    await userEvent.selectOptions(screen.getByLabelText('Risk'), '')
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines\?alert=none$/)
  })

  it('filtering really filters: only matching engines are listed', async () => {
    server.use(enginesBackend(fleet).handler)
    renderAppAt('/engines?risk=critical')
    await screen.findByText('1–20 of 20 engines')
    for (const row of rows()) expect(within(row).getAllByRole('cell')[4]).toHaveTextContent('Critical') // the Risk column
  })
})

describe('EnginesPage: pagination', () => {
  it('keeps the active filters in the URL and the requests while paging', async () => {
    const allWarning = Array.from({ length: 80 }, (_, i) => engineSummary(i + 1, { risk_category: 'warning' }))
    const backend = enginesBackend(allWarning)
    server.use(backend.handler)
    renderAppAt('/engines?risk=warning')
    expect(await screen.findByText('1–25 of 80 engines')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('26–50 of 80 engines')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines\?risk=warning&page=2$/)
    expect(backend.last().searchParams.get('risk')).toBe('warning')
    expect(backend.last().searchParams.get('offset')).toBe('25')
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('76–80 of 80 engines')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines\?risk=warning&page=4$/)
  })

  it('moves between pages with Next and Previous', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines')
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()
    expect(ids()[0]).toBe('1')
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('26–50 of 60 engines')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines\?page=2$/)
    expect(backend.last().searchParams.get('offset')).toBe('25')
    expect(ids()[0]).toBe('26') // still in engine id order across pages
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('51–60 of 60 engines')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByText('26–50 of 60 engines')).toBeInTheDocument()
  })

  it('shows a filtered result that fits on one page, with Next disabled', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines?q=1')
    expect(await screen.findByText('1–15 of 15 engines')).toBeInTheDocument() // ids containing "1": 1, 10-19, 21, 31, 41, 51
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(screen.getByTestId('location')).toHaveTextContent('/engines?q=1')
  })

  it('handles a page past the end and lets the user go back', async () => {
    server.use(enginesBackend(fleet).handler)
    renderAppAt('/engines?page=9')
    expect(await screen.findByText('This page is past the end of the list.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByText('51–60 of 60 engines')).toBeInTheDocument()
  })
})

describe('EnginesPage: empty, error and request hygiene', () => {
  it('shows an empty state when no engines exist', async () => {
    server.use(enginesBackend([]).handler)
    renderAppAt('/engines')
    expect(await screen.findByText('No engines have been ingested yet.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument()
  })

  it('shows a no-match state for filters and Clear filters restores the fleet', async () => {
    server.use(enginesBackend(fleet).handler)
    renderAppAt('/engines?q=99&risk=critical')
    expect(await screen.findByText('No engines match these filters.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines$/)
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()
    expect(screen.getByLabelText('Engine id contains')).toHaveValue('')
  })

  it('shows a backend error from the envelope and recovers with Try again', async () => {
    server.use(enginesBackend(fleet, { fail: (n) => n <= 2 }).handler) // the first call and its one automatic retry both fail
    renderAppAt('/engines')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('backend error')
    expect(alert).toHaveTextContent('Server exploded')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows "API unreachable" for a gateway failure', async () => {
    server.use(http.get('*/api/v1/engines', () => new HttpResponse(null, { status: 502 })))
    renderAppAt('/engines')
    expect(await screen.findByRole('alert')).toHaveTextContent('API unreachable')
  })

  it('shows "Unexpected response" for an unknown origin or a malformed body', async () => {
    server.use(http.get('*/api/v1/engines', () => HttpResponse.json(page([engineSummary(1, { origin: 'field' })], 1, 25, 0))))
    renderAppAt('/engines')
    expect(await screen.findByRole('alert')).toHaveTextContent('Unexpected response')
  })

  it('sends only credential-free GET requests to the engines endpoint and refetches only on Refresh', async () => {
    const backend = enginesBackend(fleet)
    server.use(backend.handler)
    renderAppAt('/engines')
    await screen.findByText('1–25 of 60 engines')
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(backend.requests).toHaveLength(2))
    for (const request of backend.requests) {
      expect(request.method).toBe('GET')
      expect(request.url.pathname).toBe('/api/v1/engines') // no simulation, alert or model endpoint is touched
      for (const name of ['x-api-key', 'x-control-key', 'authorization', 'cookie']) expect(request.headers.has(name)).toBe(false)
    }
  })
})
