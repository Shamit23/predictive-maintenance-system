import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { alertOut, engineDetail, engineSummary, page, predictionOut } from '../test/fixtures'
import { listBackend } from '../test/backends'
import { renderAppAt } from '../test/render'
import { server } from '../test/server'

// Navigation between the lists and the engine detail page, with the lists' filters and pages preserved.

type Engine = ReturnType<typeof engineSummary>
type Alert = ReturnType<typeof alertOut>

/** Detail and prediction handlers that answer for whatever engine id is requested. */
const detailHandlers = [
  http.get('*/api/v1/engines/:id', ({ params }) => HttpResponse.json({ ...engineDetail, id: Number(params.id) })),
  http.get('*/api/v1/engines/:id/predictions', () =>
    HttpResponse.json(page([predictionOut(3, 40, 'warning'), predictionOut(2, 50, 'normal'), predictionOut(1, 60, 'normal')], 3, 200, 0)),
  ),
]

const warningFleet: Engine[] = Array.from({ length: 80 }, (_, i) => engineSummary(i + 1, { risk_category: 'warning' }))
const byRisk = (rows: Engine[], p: URLSearchParams) => (p.get('risk') ? rows.filter((e) => e.risk_category === p.get('risk')) : rows)

const alerts: Alert[] = [
  alertOut(1, { engine_id: 24, severity: 'critical' }),
  alertOut(2, { engine_id: 30, severity: 'critical', status: 'acknowledged' }),
  alertOut(3, { engine_id: 31, severity: 'warning', status: 'resolved' }),
  alertOut(4, { engine_id: 24, severity: 'warning', status: 'resolved' }),
]
const alertFilters = (rows: Alert[], p: URLSearchParams) => {
  let out = rows
  const status = p.get('status')
  if (status === 'unresolved') out = out.filter((a) => a.status !== 'resolved')
  else if (status) out = out.filter((a) => a.status === status)
  if (p.get('severity')) out = out.filter((a) => a.severity === p.get('severity'))
  if (p.get('engine_id')) out = out.filter((a) => String(a.engine_id) === p.get('engine_id'))
  return out
}

const location = () => screen.getByTestId('location')

describe('from the Engines list to an engine and back', () => {
  it('links each engine id to its detail page without altering the list URL', async () => {
    server.use(listBackend('*/api/v1/engines', warningFleet, { apply: byRisk }).handler, ...detailHandlers)
    renderAppAt('/engines?risk=warning&page=2')
    const link = await screen.findByRole('link', { name: 'View details for engine 26' })
    expect(link).toHaveAttribute('href', '/engines/26')
    expect(link).toHaveTextContent(/^26$/) // the id is the visible link text
    expect(location()).toHaveTextContent(/^\/engines\?risk=warning&page=2$/) // untouched until the link is used
  })

  it('opens the engine, and "Back to engines" restores the same filters and page', async () => {
    const engines = listBackend('*/api/v1/engines', warningFleet, { apply: byRisk })
    server.use(engines.handler, ...detailHandlers)
    renderAppAt('/engines?risk=warning&page=2')
    expect(await screen.findByText('26–50 of 80 engines')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('link', { name: 'View details for engine 26' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Engine 26' })).toBeInTheDocument()
    expect(location()).toHaveTextContent(/^\/engines\/26$/)
    const back = screen.getByRole('link', { name: 'Back to engines' })
    expect(back).toHaveAttribute('href', '/engines?risk=warning&page=2')

    await userEvent.click(back)
    expect(await screen.findByText('26–50 of 80 engines')).toBeInTheDocument()
    expect(location()).toHaveTextContent(/^\/engines\?risk=warning&page=2$/)
    expect(engines.last().searchParams.get('risk')).toBe('warning')
    expect(engines.last().searchParams.get('offset')).toBe('25')
    expect(screen.getByLabelText('Risk')).toHaveValue('warning')
  })

  it('also restores an active id search', async () => {
    server.use(listBackend('*/api/v1/engines', warningFleet, { apply: (rows, p) => rows.filter((e) => String(e.id).includes(p.get('search') ?? '')) }).handler, ...detailHandlers)
    renderAppAt('/engines?q=7')
    await userEvent.click(await screen.findByRole('link', { name: 'View details for engine 7' }))
    await screen.findByRole('heading', { level: 1, name: 'Engine 7' })
    expect(screen.getByRole('link', { name: 'Back to engines' })).toHaveAttribute('href', '/engines?q=7')
    await userEvent.click(screen.getByRole('link', { name: 'Back to engines' }))
    expect(await screen.findByLabelText('Engine id contains')).toHaveValue('7')
  })

  it('can be used from the keyboard: the link takes focus, shows a focus style, and Enter opens the engine', async () => {
    server.use(listBackend('*/api/v1/engines', warningFleet).handler, ...detailHandlers)
    renderAppAt('/engines')
    const link = await screen.findByRole('link', { name: 'View details for engine 3' })
    expect(link.className).toMatch(/focus-visible:outline-2/)
    link.focus()
    expect(link).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { level: 1, name: 'Engine 3' })).toBeInTheDocument()
  })

  it('leaves paging and filtering working with the links present', async () => {
    const engines = listBackend('*/api/v1/engines', warningFleet, { apply: byRisk })
    server.use(engines.handler, ...detailHandlers)
    renderAppAt('/engines')
    expect(await screen.findByText('1–25 of 80 engines')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('26–50 of 80 engines')).toBeInTheDocument()
    expect(location()).toHaveTextContent(/^\/engines\?page=2$/)
    await userEvent.selectOptions(screen.getByLabelText('Risk'), 'warning')
    expect(location()).toHaveTextContent(/^\/engines\?risk=warning$/)
    expect(await screen.findByText('1–25 of 80 engines')).toBeInTheDocument()
  })
})

describe('from the Alerts list to an engine and back', () => {
  it('links each alert\'s engine id to its detail page', async () => {
    server.use(listBackend('*/api/v1/alerts', alerts, { apply: alertFilters }).handler, ...detailHandlers)
    renderAppAt('/alerts?status=all')
    const table = within(await screen.findByRole('region', { name: 'Alerts' }))
    const links = table.getAllByRole('link')
    expect(links.map((l) => l.getAttribute('href'))).toEqual(['/engines/24', '/engines/30', '/engines/31', '/engines/24'])
    expect(links.map((l) => l.getAttribute('aria-label'))).toEqual([
      'View details for engine 24', 'View details for engine 30', 'View details for engine 31', 'View details for engine 24',
    ])
  })

  it('opens the engine, and "Back to alerts" restores the same filters', async () => {
    const backend = listBackend('*/api/v1/alerts', alerts, { apply: alertFilters })
    server.use(backend.handler, ...detailHandlers)
    renderAppAt('/alerts?status=all&severity=critical')
    expect(await screen.findByText('1–2 of 2 alerts')).toBeInTheDocument()

    const first = within(screen.getByRole('region', { name: 'Alerts' })).getAllByRole('link')[0] as HTMLElement
    await userEvent.click(first)
    expect(await screen.findByRole('heading', { level: 1, name: 'Engine 24' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to engines' })).toHaveAttribute('href', '/engines')
    const back = screen.getByRole('link', { name: 'Back to alerts' })
    expect(back).toHaveAttribute('href', '/alerts?status=all&severity=critical')

    await userEvent.click(back)
    expect(await screen.findByText('1–2 of 2 alerts')).toBeInTheDocument()
    expect(location()).toHaveTextContent(/^\/alerts\?status=all&severity=critical$/)
    expect(backend.last().searchParams.get('severity')).toBe('critical')
    expect(backend.last().searchParams.has('status')).toBe(false) // "all" sends no status filter
    expect(screen.getByLabelText('Severity')).toHaveValue('critical')
  })

  it('keeps an engine filter from the URL when returning', async () => {
    server.use(listBackend('*/api/v1/alerts', alerts, { apply: alertFilters }).handler, ...detailHandlers)
    renderAppAt('/alerts?engine_id=24&status=all')
    const table = within(await screen.findByRole('region', { name: 'Alerts' }))
    expect(screen.getByText('Engine 24')).toBeInTheDocument()
    await userEvent.click(table.getAllByRole('link')[0] as HTMLElement)
    await screen.findByRole('heading', { level: 1, name: 'Engine 24' })
    expect(screen.getByRole('link', { name: 'Back to alerts' })).toHaveAttribute('href', '/alerts?engine_id=24&status=all')
  })
})

describe('from an engine to its alerts', () => {
  it('opens the Alerts list filtered to that engine, across all statuses', async () => {
    const backend = listBackend('*/api/v1/alerts', alerts, { apply: alertFilters })
    server.use(backend.handler, ...detailHandlers)
    renderAppAt('/engines/24')
    await userEvent.click(await screen.findByRole('link', { name: 'View alerts for this engine' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Alerts' })).toBeInTheDocument()
    expect(location()).toHaveTextContent(/^\/alerts\?engine_id=24&status=all$/)
    expect(await screen.findByText('Engine 24')).toBeInTheDocument()
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Alerts' })).getAllByRole('row').slice(1)).toHaveLength(2))
    expect(backend.last().searchParams.get('engine_id')).toBe('24')
    expect(backend.last().searchParams.has('status')).toBe(false)
    expect(screen.getByLabelText('Status')).toHaveValue('all')
  })
})
