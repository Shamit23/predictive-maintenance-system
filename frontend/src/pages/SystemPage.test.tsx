import { QueryClient } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
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

/** The <details> element of a metric group, found through its summary's group-name text. */
const groupElement = (group: string) => card('Active model').getByText(group).closest('details') as HTMLDetailsElement

/** The value cell (<dd>) of one metric row inside a group; row names such as `xgboost.mae` repeat across groups. */
const metric = (group: string, name: string) =>
  within(groupElement(group)).getByText(name).nextElementSibling as HTMLElement

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
    // Nested numeric metrics (real backend structure) render grouped; arrays are not shown.
    const active = card('Active model')
    expect(active.queryByText('The backend returned no numeric metrics for this model.')).not.toBeInTheDocument()
    expect(metric('validation_all_cycles', 'xgboost.mae')).toHaveTextContent('9.683')
    expect(metric('validation_all_cycles', 'xgboost.nasa_score')).toHaveTextContent('20288.381')
    expect(metric('validation_all_cycles', 'xgboost.n')).toHaveTextContent('3852')
    expect(metric('classification_test_last_cycle', 'xgb_classifier.f1')).toHaveTextContent('0.844')
    expect(active.queryByText(/confusion_matrix/)).not.toBeInTheDocument()
  })

  describe('metric groups', () => {
    const expected: [string, number][] = [
      ['validation_all_cycles', 12],
      ['test_last_cycle_vs_true_rul', 12],
      ['validation_final_cycle_only', 4],
      ['test_last_cycle_vs_capped_rul', 4],
      ['classification_test_last_cycle', 10],
      ['classification_validation_all_cycles', 10],
    ]

    it('renders all six groups, with their metric counts, as native details/summary', async () => {
      server.use(...okHandlers)
      renderApp()
      await card('Active model').findByText('xgb-test-1')
      const summaries = [...screen.getByRole('region', { name: 'Active model' }).querySelectorAll('details > summary')]
      expect(summaries.map((s) => s.textContent)).toEqual(expected.map(([name, count]) => `${name} (${count} metrics)`))
    })

    it('expands only the first group initially; the others are collapsed but their values stay in the DOM', async () => {
      server.use(...okHandlers)
      renderApp()
      await card('Active model').findByText('xgb-test-1')
      const groups = expected.map(([name]) => groupElement(name))
      expect(groups.map((g) => g.open)).toEqual([true, false, false, false, false, false])
      expect(metric('validation_all_cycles', 'xgboost.mae')).toBeVisible()
      expect(metric('test_last_cycle_vs_true_rul', 'xgboost.mae')).not.toBeVisible()
      // Collapsed is not removed: all 52 values are rendered inside the groups.
      expect(screen.getByRole('region', { name: 'Active model' }).querySelectorAll('details dt')).toHaveLength(52)
    })

    it('opens a collapsed group when its summary is clicked, revealing its values', async () => {
      server.use(...okHandlers)
      renderApp()
      await card('Active model').findByText('xgb-test-1')
      const summary = groupElement('classification_validation_all_cycles').querySelector('summary') as HTMLElement
      expect(metric('classification_validation_all_cycles', 'xgb_classifier.f1')).not.toBeVisible()
      await userEvent.click(summary)
      expect(groupElement('classification_validation_all_cycles').open).toBe(true)
      expect(metric('classification_validation_all_cycles', 'xgb_classifier.f1')).toBeVisible()
      expect(metric('classification_validation_all_cycles', 'xgb_classifier.f1')).toHaveTextContent('0.886')
    })

    it('makes each summary keyboard-focusable', async () => {
      server.use(...okHandlers)
      renderApp()
      await card('Active model').findByText('xgb-test-1')
      const summary = groupElement('validation_final_cycle_only').querySelector('summary') as HTMLElement
      summary.focus()
      expect(summary).toHaveFocus()
    })

    it('never shows confusion-matrix rows', async () => {
      server.use(...okHandlers)
      renderApp()
      await card('Active model').findByText('xgb-test-1')
      expect(screen.queryByText(/confusion/i)).not.toBeInTheDocument()
    })

    it('keeps the user\'s open/closed choices after a manual refresh refetches the model', async () => {
      let modelCalls = 0
      const validation = model.metrics.validation_all_cycles
      server.use(
        http.get('*/api/v1/models/active', () => {
          modelCalls += 1
          // The second response carries a changed value, proving the metrics re-rendered with fresh data.
          const metrics =
            modelCalls < 2
              ? model.metrics
              : { ...model.metrics, validation_all_cycles: { ...validation, xgboost: { ...validation.xgboost, mae: 8.5 } } }
          return HttpResponse.json({ ...model, metrics })
        }),
        ...okHandlers,
      )
      renderApp()
      await card('Active model').findByText('xgb-test-1')
      const summaryOf = (group: string) => groupElement(group).querySelector('summary') as HTMLElement

      await userEvent.click(summaryOf('test_last_cycle_vs_true_rul')) // user opens a default-closed group
      await userEvent.click(summaryOf('validation_all_cycles')) // user closes the default-open group
      const states = () => expected.map(([name]) => groupElement(name).open)
      expect(states()).toEqual([false, true, false, false, false, false])
      expect(metric('validation_all_cycles', 'xgboost.mae')).toHaveTextContent('9.683')

      await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
      await waitFor(() => expect(metric('validation_all_cycles', 'xgboost.mae')).toHaveTextContent('8.500'))
      expect(modelCalls).toBe(2)
      expect(states()).toEqual([false, true, false, false, false, false]) // unchanged by the refetch
    })

    it('keeps every row, shows no blank labels and raises no duplicate-key warning for awkward metric keys', async () => {
      const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
      server.use(
        http.get('*/api/v1/models/active', () =>
          HttpResponse.json({
            ...model,
            // `a.b` (dotted) collides with a.b (nested); leading/trailing dots cannot be split; `one` has one metric.
            metrics: { 'a.b': 1, a: { b: 2 }, '.lead': 3, 'trail.': 4, one: { v: 5 } },
          }),
        ),
        ...okHandlers,
      )
      renderApp()
      await card('Active model').findByText('xgb-test-1')

      const active = screen.getByRole('region', { name: 'Active model' })
      const summaries = [...active.querySelectorAll('details > summary')].map((s) => s.textContent)
      expect(summaries).toEqual(['a (2 metrics)', 'General (2 metrics)', 'one (1 metric)'])
      expect(active.querySelectorAll('details dt')).toHaveLength(5) // nothing lost
      const labelsAndValues = [...active.querySelectorAll('details dt')].map((dt) => [dt.textContent, dt.nextElementSibling?.textContent])
      expect(labelsAndValues).toEqual([
        ['b', '1'], // both colliding metrics survive with their own values
        ['b', '2'],
        ['.lead', '3'], // unsplittable keys keep their full key as label
        ['trail.', '4'],
        ['v', '5'],
      ])
      const duplicateKeyWarnings = consoleError.mock.calls.filter((args) => /same key|unique "key"/i.test(String(args[0])))
      expect(duplicateKeyWarnings).toEqual([])
    })
  })

  it('applies self-start to the Simulation card only', async () => {
    server.use(...okHandlers)
    renderApp()
    await screen.findByText('API healthy')
    const region = (name: string) => screen.getByRole('region', { name })
    expect(region('Simulation')).toHaveClass('self-start')
    for (const other of ['API health', 'Readiness', 'Active model']) expect(region(other)).not.toHaveClass('self-start')
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
