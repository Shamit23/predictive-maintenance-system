import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import App from '../App'
import { errorEnvelope, model, okHandlers, readyBad, simulation } from '../test/fixtures'
import { server } from '../test/server'

function renderApp() {
  // The hook's own retry policy (one retry for network/5xx) stays active; only its delay is removed for speed.
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  return render(
    <MemoryRouter>
      <App client={client} />
    </MemoryRouter>,
  )
}

const card = (name: string) => within(screen.getByRole('region', { name }))

describe('SystemPage', () => {
  it('shows loading, then real data for every card', async () => {
    server.use(...okHandlers)
    renderApp()
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0)

    await screen.findByText('API healthy')
    expect(await card('Readiness').findByText('Ready')).toBeInTheDocument()
    expect(await card('Simulation').findByText('Online')).toBeInTheDocument()
    expect(card('Simulation').getByText('No active run.')).toBeInTheDocument()
    expect(card('Simulation').getByText('#7')).toBeInTheDocument()
    expect(await card('Active model').findByText('xgb-test-1')).toBeInTheDocument()
    // Nested numeric metrics (real backend structure) are flattened to dotted keys; arrays are not shown.
    const active = card('Active model')
    expect(active.queryByText('The backend returned no numeric metrics for this model.')).not.toBeInTheDocument()
    expect(active.getByText('validation_all_cycles.xgboost.mae').nextElementSibling).toHaveTextContent('9.683')
    expect(active.getByText('validation_all_cycles.xgboost.nasa_score').nextElementSibling).toHaveTextContent('20288.381')
    expect(active.getByText('validation_all_cycles.xgboost.n').nextElementSibling).toHaveTextContent('3852')
    expect(active.getByText('classification_test_last_cycle.xgb_classifier.f1').nextElementSibling).toHaveTextContent('0.844')
    expect(active.queryByText(/confusion_matrix/)).not.toBeInTheDocument()
  })

  it('shows the empty-metrics message only when the model really has no numeric metrics', async () => {
    server.use(
      http.get('*/api/v1/models/active', () => HttpResponse.json({ ...model, metrics: {} })),
      ...okHandlers,
    )
    renderApp()
    expect(await card('Active model').findByText('The backend returned no numeric metrics for this model.')).toBeInTheDocument()
  })

  it('renders "Not ready" from a 503 ReadyOut body without treating it as an outage', async () => {
    server.use(http.get('*/ready', () => HttpResponse.json(readyBad, { status: 503 })), ...okHandlers)
    renderApp()
    expect(await card('Readiness').findByText('Not ready')).toBeInTheDocument()
    expect(card('Readiness').getByText('fail')).toBeInTheDocument()
    expect(card('Readiness').queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows offline worker and empty run states', async () => {
    server.use(
      http.get('*/api/v1/simulation', () =>
        HttpResponse.json({ ...simulation, worker_online: false, workers_online: 0, last_run: null }),
      ),
      ...okHandlers,
    )
    renderApp()
    expect(await card('Simulation').findByText('Offline')).toBeInTheDocument()
    expect(card('Simulation').getByText('No runs yet.')).toBeInTheDocument()
  })

  it('shows an empty state when no model is active', async () => {
    server.use(
      http.get('*/api/v1/models/active', () => HttpResponse.json(errorEnvelope('not_found', 'none'), { status: 404 })),
      ...okHandlers,
    )
    renderApp()
    expect(await card('Active model').findByText('No active model is registered.')).toBeInTheDocument()
  })

  it('shows "API unreachable" on network failure and keeps other cards working', async () => {
    server.use(http.get('*/health', () => HttpResponse.error()), ...okHandlers)
    renderApp()
    const alert = await card('API health').findByRole('alert')
    expect(alert).toHaveTextContent('API unreachable')
    expect(await card('Readiness').findByText('Ready')).toBeInTheDocument()
  })

  it.each([502, 504])('shows "API unreachable" for an empty-body gateway %i', async (status) => {
    server.use(http.get('*/health', () => new HttpResponse(null, { status })), ...okHandlers)
    renderApp()
    const alert = await card('API health').findByRole('alert')
    expect(alert).toHaveTextContent('API unreachable')
    expect(alert).not.toHaveTextContent('backend error')
  })

  it('shows a backend error from the standard envelope', async () => {
    server.use(
      http.get('*/api/v1/simulation', () => HttpResponse.json(errorEnvelope('internal_error', 'Server exploded'), { status: 500 })),
      ...okHandlers,
    )
    renderApp()
    const alert = await card('Simulation').findByRole('alert')
    expect(alert).toHaveTextContent('backend error')
    expect(alert).toHaveTextContent('Server exploded')
  })

  it('shows "Unexpected response" when validation fails, without crashing the page', async () => {
    server.use(http.get('*/api/v1/models/active', () => HttpResponse.json({ version: 1 })), ...okHandlers)
    renderApp()
    expect(await card('Active model').findByRole('alert')).toHaveTextContent('Unexpected response')
    expect(screen.getByRole('heading', { name: 'System status' })).toBeInTheDocument()
  })

  it('manual refresh refetches all endpoints', async () => {
    let healthCalls = 0
    server.use(
      http.get('*/health', () => {
        healthCalls += 1
        return HttpResponse.json({ status: 'ok' })
      }),
      ...okHandlers,
    )
    renderApp()
    await screen.findByText('API healthy')
    const before = healthCalls
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(healthCalls).toBe(before + 1))
  })

  it('does not use browser storage or cookies', async () => {
    server.use(...okHandlers)
    renderApp()
    await screen.findByText('API healthy')
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    expect(document.cookie).toBe('')
  })

  it('renders a not-found page for unknown routes', () => {
    const client = new QueryClient()
    render(
      <MemoryRouter initialEntries={['/nope']}>
        <App client={client} />
      </MemoryRouter>,
    )
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})
