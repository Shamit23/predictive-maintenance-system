import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { alertOut, archivedModel, deferred, engineSummary } from '../test/fixtures'
import { listBackend } from '../test/backends'
import { renderAppAt } from '../test/render'
import { server } from '../test/server'

// Behaviour shared by the three paged lists (Engines, Alerts, Models): what the page shows while a new filter or page is
// loading (stale rows must not look like the new result), how a failed request is shown, and what is announced.

type Engine = ReturnType<typeof engineSummary>
type Alert = ReturnType<typeof alertOut>

const risks = ['normal', 'warning', 'critical'] as const
/** 60 engines, ids 1..60, cycling through the risk levels: 20 of each. */
const fleet: Engine[] = Array.from({ length: 60 }, (_, i) => engineSummary(i + 1, { risk_category: risks[i % 3] }))
const byRisk = (rows: Engine[], p: URLSearchParams) => (p.get('risk') ? rows.filter((e) => e.risk_category === p.get('risk')) : rows)

/** 60 open alerts, alternating warning and critical severity (30 each). */
const alerts: Alert[] = Array.from({ length: 60 }, (_, i) => alertOut(i + 1, { engine_id: i + 1, severity: i % 2 === 0 ? 'warning' : 'critical' }))
const bySeverity = (rows: Alert[], p: URLSearchParams) => (p.get('severity') ? rows.filter((a) => a.severity === p.get('severity')) : rows)

const versions = Array.from({ length: 23 }, (_, i) => ({ ...archivedModel, version: `v-${String(i + 1).padStart(2, '0')}` }))

const engineIds = () =>
  within(screen.getByRole('region', { name: 'Engines' }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[0]?.textContent)
const alertEngines = () =>
  within(screen.getByRole('region', { name: 'Alerts' }))
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell')[2]?.textContent)
const busyArea = (regionName: string) => screen.getByRole('region', { name: regionName }).closest('[aria-busy]') as HTMLElement
const refreshButton = () => screen.getByRole('button', { name: /^Refresh/ })

// aria-busy tells assistive tech to hold back changes inside a region until it is false, so it must wrap only the
// DISPLAYED results. The initial loading message and every error alert must stay outside it, or they could go unheard.
const lists = [
  { name: 'Engines', path: '/engines', pattern: '*/api/v1/engines', items: fleet as unknown[], loading: 'Loading engines…', region: 'Engines' },
  { name: 'Alerts', path: '/alerts', pattern: '*/api/v1/alerts', items: alerts as unknown[], loading: 'Loading alerts…', region: 'Alerts' },
  { name: 'Models', path: '/models', pattern: '*/api/v1/models', items: versions as unknown[], loading: 'Loading models…', region: 'v-01' },
]
const insideBusy = (element: HTMLElement) => element.closest('[aria-busy="true"]')

describe.each(lists)('$name: aria-busy placement', ({ path, pattern, items, loading, region }) => {
  it('keeps the initial loading message outside any busy region, then wraps only the displayed results', async () => {
    const gate = deferred()
    server.use(listBackend(pattern, items, { hold: (n) => (n === 1 ? gate.promise : undefined) }).handler)
    renderAppAt(path)
    const status = await screen.findByRole('status')
    expect(status).toHaveTextContent(loading)
    expect(insideBusy(status)).toBeNull()
    expect(document.querySelector('main [aria-busy]')).toBeNull() // nothing is marked busy while only loading

    await act(async () => gate.release())
    await screen.findByRole('region', { name: region })
    expect(busyArea(region)).toHaveAttribute('aria-busy', 'false')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('keeps the initial error alert outside any busy region', async () => {
    server.use(listBackend(pattern, items, { fail: () => true }).handler)
    renderAppAt(path)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Server exploded')
    expect(insideBusy(alert)).toBeNull()
    expect(document.querySelector('main [aria-busy]')).toBeNull()
  })

  it('keeps a failed-refresh notice outside the busy region while the old results stay inside it', async () => {
    server.use(listBackend(pattern, items, { fail: (n) => n === 2 || n === 3 }).handler)
    renderAppAt(path)
    await screen.findByRole('region', { name: region })
    await userEvent.click(refreshButton())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Server exploded')
    expect(insideBusy(alert)).toBeNull()
    expect(alert.closest('[aria-busy]')).toBeNull() // outside the results wrapper altogether, not just "not busy"
    expect(screen.getByText('Showing the last successful result.')).toBeInTheDocument()
    expect(busyArea(region)).toHaveAttribute('aria-busy', 'false') // the old results are still shown, in their own wrapper
  })

  it('marks the displayed results busy while a refresh is in flight, without calling them stale', async () => {
    const gate = deferred()
    server.use(listBackend(pattern, items, { hold: (n) => (n === 2 ? gate.promise : undefined) }).handler)
    renderAppAt(path)
    await screen.findByRole('region', { name: region })
    await userEvent.click(refreshButton())
    expect(busyArea(region)).toHaveAttribute('aria-busy', 'true')
    expect(busyArea(region)).not.toHaveClass('opacity-50') // same filters and page: not stale, so not dimmed
    expect(screen.queryByText('Updating…')).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument() // no loading message replaces the results
    await act(async () => gate.release())
    await waitFor(() => expect(busyArea(region)).toHaveAttribute('aria-busy', 'false'))
  })
})

describe('Stale results stay identified as updating, with the notice outside the busy region', () => {
  it('Engines: the "Updating…" line and live region are outside the busy wrapper that holds the dimmed old rows', async () => {
    const gate = deferred()
    server.use(listBackend('*/api/v1/engines', fleet, { apply: byRisk, hold: (n) => (n === 2 ? gate.promise : undefined) }).handler)
    renderAppAt('/engines')
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Risk'), 'critical')
    expect(busyArea('Engines')).toHaveAttribute('aria-busy', 'true')
    expect(busyArea('Engines')).toHaveClass('opacity-50')
    expect(insideBusy(screen.getByText('Updating…'))).toBeNull()
    expect(insideBusy(screen.getByText('Updating results…'))).toBeNull()
    await act(async () => gate.release())
    expect(await screen.findByText('1–20 of 20 engines')).toBeInTheDocument()
    expect(busyArea('Engines')).toHaveAttribute('aria-busy', 'false')
  })
})

describe('Engines: stale rows while loading', () => {
  it('keeps a consistent, clearly busy view while the next page loads, then updates everything together', async () => {
    const gate = deferred()
    server.use(listBackend('*/api/v1/engines', fleet, { hold: (n) => (n === 2 ? gate.promise : undefined) }).handler)
    renderAppAt('/engines')
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()
    expect(screen.getByText('60 engines found. Showing 1 to 25.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    // The URL has moved on, but the rows and the pager still describe page 1, and the page says it is updating.
    expect(screen.getByTestId('location')).toHaveTextContent('/engines?page=2')
    expect(engineIds()[0]).toBe('1')
    expect(screen.getByText('1–25 of 60 engines')).toBeInTheDocument()
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()
    expect(screen.queryByText('26–50 of 60 engines')).not.toBeInTheDocument() // the new offset is not mixed with old totals
    expect(screen.getByText('Updating…')).toBeVisible()
    expect(screen.getByText('Updating results…')).toHaveAttribute('aria-live', 'polite')
    expect(busyArea('Engines')).toHaveAttribute('aria-busy', 'true')
    expect(busyArea('Engines')).toHaveClass('opacity-50')

    // A second click while loading repeats the same move instead of skipping a page.
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines\?page=2$/)

    await act(async () => gate.release())
    expect(await screen.findByText('26–50 of 60 engines')).toBeInTheDocument()
    expect(engineIds()[0]).toBe('26')
    expect(screen.queryByText('Updating…')).not.toBeInTheDocument()
    expect(busyArea('Engines')).toHaveAttribute('aria-busy', 'false')
    expect(busyArea('Engines')).not.toHaveClass('opacity-50')
    expect(screen.getByText('60 engines found. Showing 26 to 50.')).toBeInTheDocument()
  })

  it('marks old rows as updating after a filter change, so they are not mistaken for the filtered result', async () => {
    const gate = deferred()
    const backend = listBackend('*/api/v1/engines', fleet, { apply: byRisk, hold: (n) => (n === 2 ? gate.promise : undefined) })
    server.use(backend.handler)
    renderAppAt('/engines')
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Risk'), 'critical')
    expect(screen.getByLabelText('Risk')).toHaveValue('critical') // the control already shows the new filter...
    expect(screen.getByText('Updating…')).toBeVisible() // ...and the rows are labelled as not yet its result
    expect(busyArea('Engines')).toHaveClass('opacity-50')
    expect(engineIds()).toContain('1') // engine 1 is "normal": it belongs to the old result only

    await act(async () => gate.release())
    expect(await screen.findByText('1–20 of 20 engines')).toBeInTheDocument()
    expect(engineIds()).not.toContain('1')
    expect(screen.queryByText('Updating…')).not.toBeInTheDocument()
    expect(screen.getByText('20 engines found. Showing 1 to 20.')).toBeInTheDocument()
  })

  it('shows an error, never the old rows, when the request for the new filter fails', async () => {
    server.use(listBackend('*/api/v1/engines', fleet, { apply: byRisk, fail: (n) => n >= 2 }).handler)
    renderAppAt('/engines')
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Risk'), 'critical')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Server exploded')
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Engines' })).not.toBeInTheDocument() // the stale table is gone
    expect(screen.queryByText('Showing the last successful result.')).not.toBeInTheDocument()
    expect(screen.queryByText('Updating…')).not.toBeInTheDocument()
  })
})

describe('Engines: a refresh that fails while data is on screen', () => {
  it('keeps the rows, says they are the last successful result, and recovers on the next refresh', async () => {
    // Request 1 loads the page; requests 2 and 3 (a refresh and its one automatic retry) fail; later ones succeed.
    server.use(listBackend('*/api/v1/engines', fleet, { fail: (n) => n === 2 || n === 3 }).handler)
    renderAppAt('/engines')
    expect(await screen.findByText('1–25 of 60 engines')).toBeInTheDocument()

    await userEvent.click(refreshButton())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('backend error')
    expect(alert).toHaveTextContent('Server exploded')
    expect(screen.getByText('Showing the last successful result.')).toBeInTheDocument()
    expect(engineIds()).toHaveLength(25) // the previous rows are still there
    expect(screen.getByText('1–25 of 60 engines')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument() // Refresh is the retry here

    await waitFor(() => expect(refreshButton()).toBeEnabled())
    await userEvent.click(refreshButton())
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.queryByText('Showing the last successful result.')).not.toBeInTheDocument()
    expect(engineIds()).toHaveLength(25)
  })
})

describe('Engines: Clear filters resets the search box too', () => {
  it('empties an unsubmitted draft', async () => {
    server.use(listBackend('*/api/v1/engines', [] as Engine[]).handler)
    renderAppAt('/engines?risk=critical')
    expect(await screen.findByText('No engines match these filters.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Engine id contains'), '123')
    expect(screen.getByLabelText('Engine id contains')).toHaveValue('123')
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByLabelText('Engine id contains')).toHaveValue('')
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines$/)
  })

  it('also removes a visible validation error and the invalid text', async () => {
    server.use(listBackend('*/api/v1/engines', [] as Engine[]).handler)
    renderAppAt('/engines?risk=critical')
    expect(await screen.findByText('No engines match these filters.')).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Engine id contains'), 'abc')
    await userEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(await screen.findByText('Enter digits only (at most 20).')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.queryByText('Enter digits only (at most 20).')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Engine id contains')).toHaveValue('')
    expect(screen.getByLabelText('Engine id contains')).toHaveAttribute('aria-invalid', 'false')
  })

  it('still clears a submitted search from the URL', async () => {
    server.use(listBackend('*/api/v1/engines', [] as Engine[]).handler)
    renderAppAt('/engines?q=24&risk=critical')
    expect(await screen.findByText('No engines match these filters.')).toBeInTheDocument()
    expect(screen.getByLabelText('Engine id contains')).toHaveValue('24')
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByLabelText('Engine id contains')).toHaveValue('')
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/engines$/)
  })
})

describe('Engines: result announcements', () => {
  it('announces the count after load and again after a filter changes it', async () => {
    server.use(listBackend('*/api/v1/engines', fleet, { apply: byRisk }).handler)
    renderAppAt('/engines')
    expect(await screen.findByText('60 engines found. Showing 1 to 25.')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Risk'), 'warning')
    expect(await screen.findByText('20 engines found. Showing 1 to 20.')).toBeInTheDocument()
    expect(screen.queryByText('60 engines found. Showing 1 to 25.')).not.toBeInTheDocument()
  })

  it('announces an empty result', async () => {
    server.use(listBackend('*/api/v1/engines', [] as Engine[]).handler)
    renderAppAt('/engines')
    expect(await screen.findByText('No engines found.')).toBeInTheDocument()
  })

  it('announces a single engine in the singular', async () => {
    server.use(listBackend('*/api/v1/engines', [engineSummary(7)]).handler)
    renderAppAt('/engines')
    expect(await screen.findByText('1 engine found. Showing 1 to 1.')).toBeInTheDocument()
    expect(screen.getByText('1 of 1 engine')).toBeInTheDocument()
  })

  it('announces a page past the end', async () => {
    server.use(listBackend('*/api/v1/engines', fleet).handler)
    renderAppAt('/engines?page=9')
    expect(await screen.findByText('No engines on this page.')).toBeInTheDocument()
  })
})

describe('Alerts: stale rows, failed requests and announcements', () => {
  it('marks old rows as updating after a filter change and announces the new count', async () => {
    const gate = deferred()
    server.use(listBackend('*/api/v1/alerts', alerts, { apply: bySeverity, hold: (n) => (n === 2 ? gate.promise : undefined) }).handler)
    renderAppAt('/alerts')
    expect(await screen.findByText('1–25 of 60 alerts')).toBeInTheDocument()
    expect(screen.getByText('60 alerts found. Showing 1 to 25.')).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'critical')
    expect(screen.getByText('Updating…')).toBeVisible()
    expect(busyArea('Alerts')).toHaveClass('opacity-50')
    expect(screen.getByText('1–25 of 60 alerts')).toBeInTheDocument() // pager still matches the rows on screen
    expect(alertEngines()[0]).toBe('1') // engine 1's alert is a warning: part of the old result only

    await act(async () => gate.release())
    expect(await screen.findByText('1–25 of 30 alerts')).toBeInTheDocument()
    expect(screen.queryByText('Updating…')).not.toBeInTheDocument()
    expect(alertEngines()[0]).toBe('2') // the first critical alert
    expect(screen.getByText('30 alerts found. Showing 1 to 25.')).toBeInTheDocument()
  })

  it('keeps the pager consistent with the rows while the next page loads', async () => {
    const gate = deferred()
    server.use(listBackend('*/api/v1/alerts', alerts, { hold: (n) => (n === 2 ? gate.promise : undefined) }).handler)
    renderAppAt('/alerts')
    expect(await screen.findByText('1–25 of 60 alerts')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByText('1–25 of 60 alerts')).toBeInTheDocument()
    expect(screen.queryByText('26–50 of 60 alerts')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/alerts\?page=2$/)
    await act(async () => gate.release())
    expect(await screen.findByText('26–50 of 60 alerts')).toBeInTheDocument()
  })

  it('shows an error, never the old rows, when the request for the new filter fails', async () => {
    server.use(listBackend('*/api/v1/alerts', alerts, { apply: bySeverity, fail: (n) => n >= 2 }).handler)
    renderAppAt('/alerts')
    expect(await screen.findByText('1–25 of 60 alerts')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'critical')
    expect(await screen.findByRole('alert')).toHaveTextContent('Server exploded')
    expect(screen.queryByRole('region', { name: 'Alerts' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('keeps the rows and says so when a refresh fails, then recovers', async () => {
    server.use(listBackend('*/api/v1/alerts', alerts, { fail: (n) => n === 2 || n === 3 }).handler)
    renderAppAt('/alerts')
    expect(await screen.findByText('1–25 of 60 alerts')).toBeInTheDocument()
    await userEvent.click(refreshButton())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('backend error')
    expect(alert).toHaveTextContent('Server exploded')
    expect(screen.getByText('Showing the last successful result.')).toBeInTheDocument()
    expect(alertEngines()).toHaveLength(25)
    await waitFor(() => expect(refreshButton()).toBeEnabled())
    await userEvent.click(refreshButton())
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
    expect(screen.queryByText('Showing the last successful result.')).not.toBeInTheDocument()
  })

  it('announces empty and singular results', async () => {
    server.use(listBackend('*/api/v1/alerts', [] as Alert[]).handler)
    renderAppAt('/alerts')
    expect(await screen.findByText('No alerts found.')).toBeInTheDocument()
  })

  it('announces a single alert in the singular', async () => {
    server.use(listBackend('*/api/v1/alerts', [alertOut(1)]).handler)
    renderAppAt('/alerts')
    expect(await screen.findByText('1 alert found. Showing 1 to 1.')).toBeInTheDocument()
    expect(screen.getByText('1 of 1 alert')).toBeInTheDocument()
  })
})

describe('Models: stale cards, failed refresh and announcements', () => {
  it('keeps the pager consistent with the cards while the next page loads', async () => {
    const gate = deferred()
    server.use(listBackend('*/api/v1/models', versions, { hold: (n) => (n === 2 ? gate.promise : undefined) }).handler)
    renderAppAt('/models')
    expect(await screen.findByText('1–10 of 23 model versions')).toBeInTheDocument()
    expect(screen.getByText('23 model versions found. Showing 1 to 10.')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/models?page=2')
    expect(screen.getByText('Updating…')).toBeVisible()
    expect(screen.getByRole('region', { name: 'v-01' })).toBeInTheDocument() // still page 1's cards
    expect(screen.getByText('1–10 of 23 model versions')).toBeInTheDocument()
    expect(screen.queryByText('11–20 of 23 model versions')).not.toBeInTheDocument()
    expect(busyArea('v-01')).toHaveClass('opacity-50')

    await act(async () => gate.release())
    expect(await screen.findByText('11–20 of 23 model versions')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'v-11' })).toBeInTheDocument()
    expect(screen.queryByText('Updating…')).not.toBeInTheDocument()
  })

  it('keeps the cards and says so when a refresh fails', async () => {
    server.use(listBackend('*/api/v1/models', versions, { fail: (n) => n === 2 || n === 3 }).handler)
    renderAppAt('/models')
    expect(await screen.findByText('1–10 of 23 model versions')).toBeInTheDocument()
    await userEvent.click(refreshButton())
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Server exploded')
    expect(screen.getByText('Showing the last successful result.')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'v-01' })).toBeInTheDocument()
    expect(screen.getByText('1–10 of 23 model versions')).toBeInTheDocument()
  })

  it('uses the singular for a single model version', async () => {
    server.use(listBackend('*/api/v1/models', versions.slice(0, 1)).handler)
    renderAppAt('/models')
    expect(await screen.findByText('1 model version found. Showing 1 to 1.')).toBeInTheDocument()
    expect(screen.getByText('1 of 1 model version')).toBeInTheDocument()
  })
})
