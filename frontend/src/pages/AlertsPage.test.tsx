import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { formatDateTime } from '../lib/format'
import { alertOut, errorEnvelope, page } from '../test/fixtures'
import type { BackendOptions } from '../test/fixtures'
import { renderAppAt } from '../test/render'
import { server } from '../test/server'

type Alert = ReturnType<typeof alertOut>

interface Recorded {
  url: URL
  method: string
  headers: Headers
}

/**
 * A backend stand-in for GET /api/v1/alerts that applies status, severity, engine_id, limit and offset the way the
 * real endpoint does, and records every request it receives.
 */
function alertsBackend(all: Alert[], { fail = () => false, hold }: BackendOptions = {}) {
  const requests: Recorded[] = []
  const handler = http.get('*/api/v1/alerts', async ({ request }) => {
    const url = new URL(request.url)
    requests.push({ url, method: request.method, headers: request.headers })
    const number = requests.length
    await hold?.(number)
    if (fail(number)) {
      return HttpResponse.json(errorEnvelope('internal_error', 'Server exploded'), { status: 500 })
    }
    const p = url.searchParams
    let rows = all
    const status = p.get('status')
    if (status === 'unresolved') rows = rows.filter((a) => a.status === 'open' || a.status === 'acknowledged')
    else if (status) rows = rows.filter((a) => a.status === status)
    if (p.get('severity')) rows = rows.filter((a) => a.severity === p.get('severity'))
    if (p.get('engine_id')) rows = rows.filter((a) => String(a.engine_id) === p.get('engine_id'))
    const limit = Number(p.get('limit'))
    const offset = Number(p.get('offset'))
    return HttpResponse.json(page(rows.slice(offset, offset + limit), rows.length, limit, offset))
  })
  return { handler, requests, last: () => requests[requests.length - 1]?.url as URL }
}

const sample: Alert[] = [
  alertOut(1, { engine_id: 24, severity: 'warning', status: 'open', predicted_rul: 45.5 }),
  alertOut(2, {
    engine_id: 30, severity: 'critical', status: 'acknowledged', predicted_rul: 12.3, model_version: 'xgb-test-2',
    acknowledged_at: '2026-01-01T11:00:00Z', updated_at: '2026-01-01T11:00:00Z',
  }),
  alertOut(3, {
    engine_id: 31, severity: 'warning', status: 'resolved', resolved_at: '2026-01-02T00:00:00Z', resolution_reason: 'recovered',
  }),
  alertOut(4, {
    engine_id: 32, severity: 'warning', status: 'resolved', resolved_at: '2026-01-03T00:00:00Z', resolution_reason: 'escalated',
  }),
]

/** Many open alerts, for pagination. */
const many: Alert[] = Array.from({ length: 60 }, (_, i) => alertOut(i + 1, { engine_id: i + 1 }))

const table = () => within(screen.getByRole('region', { name: 'Alerts' }))
const rows = () => table().getAllByRole('row').slice(1) // without the header row
/** Waits (retrying the lookup too) until the table contains this text. */
const tableText = (text: string) => waitFor(() => expect(table().getByText(text)).toBeInTheDocument())

describe('AlertsPage: default view and data', () => {
  it('requests unresolved alerts, 25 per page, with nothing else', async () => {
    const backend = alertsBackend(sample)
    server.use(backend.handler)
    renderAppAt('/alerts')
    expect(screen.getByRole('status')).toHaveTextContent('Loading alerts…')
    await tableText('45.5')
    expect(backend.requests).toHaveLength(1)
    expect(backend.last().search).toBe('?status=unresolved&limit=25&offset=0')
    expect(screen.getByLabelText('Status')).toHaveValue('unresolved')
    expect(screen.getByLabelText('Severity')).toHaveValue('')
  })

  it('shows only unresolved alerts by default, with every column', async () => {
    server.use(alertsBackend(sample).handler)
    renderAppAt('/alerts')
    await tableText('45.5')
    expect(rows()).toHaveLength(2) // the two resolved alerts are not part of the default view
    const [open, acknowledged] = rows() as [HTMLElement, HTMLElement]
    const first = within(open)
    expect(first.getByText('Warning')).toBeInTheDocument()
    expect(first.getByText('Open')).toBeInTheDocument()
    expect(first.getByText('24')).toBeInTheDocument()
    expect(first.getByText('45.5')).toBeInTheDocument()
    expect(first.getByText('xgb-test-1')).toBeInTheDocument()
    expect(first.getAllByText(formatDateTime('2026-01-01T10:00:00Z'))).toHaveLength(2) // created and updated
    const second = within(acknowledged)
    expect(second.getByText('Critical')).toBeInTheDocument()
    expect(second.getByText('Acknowledged', { selector: 'span' })).toBeInTheDocument()
    expect(second.getByText('12.3')).toBeInTheDocument()
    expect(second.getByText('xgb-test-2')).toBeInTheDocument()
    expect(second.getAllByText(formatDateTime('2026-01-01T11:00:00Z'))).toHaveLength(2) // updated and acknowledged
  })

  it('shows resolution time and reason for resolved alerts', async () => {
    server.use(alertsBackend(sample).handler)
    renderAppAt('/alerts?status=resolved')
    await tableText('Recovered')
    expect(rows()).toHaveLength(2)
    expect(within(rows()[0] as HTMLElement).getByText(formatDateTime('2026-01-02T00:00:00Z'))).toBeInTheDocument()
    expect(within(rows()[0] as HTMLElement).getByText('Recovered')).toBeInTheDocument()
    expect(within(rows()[1] as HTMLElement).getByText('Escalated (replaced by a new alert)')).toBeInTheDocument()
  })

  it('states the read-only nature and offers no acknowledge control', async () => {
    server.use(alertsBackend(sample).handler)
    renderAppAt('/alerts')
    await tableText('45.5')
    expect(screen.getByText(/acknowledging alerts is not available in this interface/i)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /acknowledge/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /acknowledge/i })).not.toBeInTheDocument()
  })

  it('has accessible table structure, with each engine id linking to its detail page and nothing else linked', async () => {
    server.use(alertsBackend(sample).handler)
    renderAppAt('/alerts')
    await tableText('45.5')
    expect(table().getAllByRole('columnheader').map((h) => h.textContent)).toEqual([
      'Severity', 'Status', 'Engine', 'Predicted RUL (cycles)', 'Model version', 'Created', 'Updated', 'Acknowledged', 'Resolved',
    ])
    expect(table().getByRole('table')).toHaveAccessibleName('Alerts, newest first')
    // Exactly one link per row, to that alert's engine; no other links inside the table.
    const links = table().getAllByRole('link')
    expect(links).toHaveLength(rows().length)
    expect(links.map((l) => l.getAttribute('href'))).toEqual(['/engines/24', '/engines/30'])
    expect(links.map((l) => l.textContent)).toEqual(['24', '30'])
  })
})

describe('AlertsPage: filters in the URL', () => {
  it('maps status "all" to no status filter and other statuses to themselves', async () => {
    const backend = alertsBackend(sample)
    server.use(backend.handler)
    renderAppAt('/alerts?status=all')
    await waitFor(() => expect(rows()).toHaveLength(4))
    expect(backend.last().searchParams.has('status')).toBe(false)
    expect(screen.getByLabelText('Status')).toHaveValue('all')
  })

  it.each([
    ['resolved', 'resolved'],
    ['open', 'open'],
    ['acknowledged', 'acknowledged'],
  ])('sends status=%s', async (value, expected) => {
    const backend = alertsBackend(sample)
    server.use(backend.handler)
    renderAppAt(`/alerts?status=${value}`)
    await waitFor(() => expect(backend.requests).toHaveLength(1))
    expect(backend.last().searchParams.get('status')).toBe(expected)
  })

  it('applies severity and engine filters from the URL', async () => {
    const backend = alertsBackend(sample)
    server.use(backend.handler)
    renderAppAt('/alerts?status=all&severity=critical&engine_id=30')
    await tableText('12.3')
    expect(backend.last().searchParams.get('severity')).toBe('critical')
    expect(backend.last().searchParams.get('engine_id')).toBe('30')
    expect(screen.getByLabelText('Severity')).toHaveValue('critical')
    expect(screen.getByText('Engine 30')).toBeInTheDocument()
  })

  it.each([
    ['status=bogus', '?status=unresolved&limit=25&offset=0'],
    ['status=OPEN', '?status=unresolved&limit=25&offset=0'],
    ['severity=SEVERE', '?status=unresolved&limit=25&offset=0'],
    ['severity=', '?status=unresolved&limit=25&offset=0'],
    ['engine_id=abc', '?status=unresolved&limit=25&offset=0'],
    ['engine_id=-4', '?status=unresolved&limit=25&offset=0'],
    ['engine_id=0', '?status=unresolved&limit=25&offset=0'],
    ['page=abc', '?status=unresolved&limit=25&offset=0'],
    ['page=-1', '?status=unresolved&limit=25&offset=0'],
    ['page=1.5', '?status=unresolved&limit=25&offset=0'],
    ['limit=500&offset=999', '?status=unresolved&limit=25&offset=0'],
  ])('ignores invalid URL parameters (%s) and sends a normal request', async (query, expected) => {
    const backend = alertsBackend(sample)
    server.use(backend.handler)
    renderAppAt(`/alerts?${query}`)
    await tableText('45.5')
    expect(backend.requests).toHaveLength(1)
    expect(backend.last().search).toBe(expected)
  })

  it('changing a filter updates the URL and the request, and returns to page 1', async () => {
    const backend = alertsBackend(many)
    server.use(backend.handler)
    renderAppAt('/alerts?page=2')
    await screen.findByText('26–50 of 60 alerts')
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'critical')
    expect(screen.getByTestId('location')).toHaveTextContent('/alerts?severity=critical')
    expect(screen.getByTestId('location')).not.toHaveTextContent('page=')
    await waitFor(() => expect(backend.last().searchParams.get('severity')).toBe('critical'))
    expect(backend.last().searchParams.get('offset')).toBe('0')
  })

  it('selecting the default status removes it from the URL; "All" writes status=all', async () => {
    server.use(alertsBackend(sample).handler)
    renderAppAt('/alerts?status=resolved')
    await tableText('Recovered')
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'all')
    expect(screen.getByTestId('location')).toHaveTextContent('/alerts?status=all')
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'unresolved')
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/alerts$/)
  })

  it('offers the engine filter chip and clears it', async () => {
    const backend = alertsBackend(sample)
    server.use(backend.handler)
    renderAppAt('/alerts?engine_id=24')
    await tableText('45.5')
    expect(rows()).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Clear engine filter' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/alerts$/)
    await waitFor(() => expect(rows()).toHaveLength(2))
    expect(backend.last().searchParams.has('engine_id')).toBe(false)
    expect(screen.queryByText('Engine 24')).not.toBeInTheDocument()
  })
})

describe('AlertsPage: pagination', () => {
  it('pages through results and keeps the filters in the URL', async () => {
    const backend = alertsBackend(many)
    server.use(backend.handler)
    renderAppAt('/alerts?severity=warning')
    expect(await screen.findByText('1–25 of 60 alerts')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('26–50 of 60 alerts')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/alerts?severity=warning&page=2')
    expect(backend.last().searchParams.get('offset')).toBe('25')
    expect(backend.last().searchParams.get('severity')).toBe('warning')
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('51–60 of 60 alerts')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByText('1–25 of 60 alerts')).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/alerts\?severity=warning$/)
  })

  it('handles a page past the end and lets the user go back', async () => {
    server.use(alertsBackend(many).handler)
    renderAppAt('/alerts?page=9')
    expect(await screen.findByText('This page is past the end of the list.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByText('51–60 of 60 alerts')).toBeInTheDocument()
  })
})

describe('AlertsPage: empty, error and request hygiene', () => {
  it('shows a reassuring empty state for the default view', async () => {
    server.use(alertsBackend([]).handler)
    renderAppAt('/alerts')
    expect(await screen.findByText('No unresolved alerts.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Clear filters' })).not.toBeInTheDocument()
  })

  it('shows a no-match state for filters and Clear filters returns to the default view', async () => {
    const backend = alertsBackend([sample[0] as Alert])
    server.use(backend.handler)
    renderAppAt('/alerts?severity=critical')
    expect(await screen.findByText('No alerts match these filters.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/alerts$/)
    await tableText('45.5')
  })

  it('shows a backend error from the envelope and recovers with Try again', async () => {
    server.use(alertsBackend(sample, { fail: (n) => n <= 2 }).handler) // the first call and its one automatic retry both fail
    renderAppAt('/alerts')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('backend error')
    expect(alert).toHaveTextContent('Server exploded')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await tableText('45.5')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows "API unreachable" for a gateway failure', async () => {
    server.use(http.get('*/api/v1/alerts', () => new HttpResponse(null, { status: 504 })))
    renderAppAt('/alerts')
    expect(await screen.findByRole('alert')).toHaveTextContent('API unreachable')
  })

  it('shows "Unexpected response" when validation fails', async () => {
    server.use(http.get('*/api/v1/alerts', () => HttpResponse.json({ items: [{ id: 'x' }], total: 1, limit: 25, offset: 0 })))
    renderAppAt('/alerts')
    expect(await screen.findByRole('alert')).toHaveTextContent('Unexpected response')
  })

  it('sends only credential-free GET requests to the alerts endpoint and refetches only on Refresh', async () => {
    const backend = alertsBackend(sample)
    server.use(backend.handler)
    renderAppAt('/alerts')
    await tableText('45.5')
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(backend.requests).toHaveLength(2))
    for (const request of backend.requests) {
      expect(request.method).toBe('GET')
      expect(request.url.pathname).toBe('/api/v1/alerts') // no simulation, engine or other endpoint is touched
      for (const name of ['x-api-key', 'x-control-key', 'authorization', 'cookie']) expect(request.headers.has(name)).toBe(false)
    }
  })
})
