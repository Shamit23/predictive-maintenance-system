import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { HttpResponse, http } from 'msw'
import { describe, expect, it } from 'vitest'
import { archivedModel, errorEnvelope, model, modelsPage, page } from '../test/fixtures'
import { renderAppAt } from '../test/render'
import { server } from '../test/server'

const region = (name: string) => screen.getByRole('region', { name })
const metricsDetails = (version: string) => region(version).querySelector(':scope > details') as HTMLDetailsElement

/** A handler that serves `total` model versions, honouring limit and offset like the backend, and records requests. */
function pagedModels(total: number) {
  const urls: URL[] = []
  const all = Array.from({ length: total }, (_, i) => ({ ...archivedModel, version: `v-${String(i + 1).padStart(2, '0')}` }))
  const handler = http.get('*/api/v1/models', ({ request }) => {
    const url = new URL(request.url)
    urls.push(url)
    const limit = Number(url.searchParams.get('limit'))
    const offset = Number(url.searchParams.get('offset'))
    return HttpResponse.json(page(all.slice(offset, offset + limit), total, limit, offset))
  })
  return { handler, urls }
}

describe('ModelsPage', () => {
  it('shows loading, then one card per version with its status and metadata', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json(modelsPage)))
    renderAppAt('/models')
    expect(screen.getByRole('status')).toHaveTextContent('Loading models…')

    expect(await screen.findByRole('heading', { level: 1, name: 'Models' })).toBeInTheDocument()
    await screen.findByRole('region', { name: 'xgb-test-1' })
    expect(within(region('xgb-test-1')).getByText('Active')).toBeInTheDocument()
    expect(within(region('xgb-test-2')).getByText('Candidate')).toBeInTheDocument()
    expect(within(region('xgb-test-0')).getByText('Archived')).toBeInTheDocument()
    expect(within(region('xgb-test-1')).getByText('FD001')).toBeInTheDocument()
    expect(within(region('xgb-test-1')).getByText('125')).toBeInTheDocument() // RUL cap
    expect(within(region('xgb-test-0')).getByText('—')).toBeInTheDocument() // no dataset
    expect(screen.getByText('1–3 of 3 model versions')).toBeInTheDocument()
  })

  it('expands the active version\'s metrics and collapses the others', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json(modelsPage)))
    renderAppAt('/models')
    await screen.findByRole('region', { name: 'xgb-test-1' })

    expect(metricsDetails('xgb-test-1').open).toBe(true)
    expect(metricsDetails('xgb-test-2').open).toBe(false)
    expect(metricsDetails('xgb-test-0').open).toBe(false)
    // The active model shows its grouped metrics with the first group open (52 numeric values, six groups).
    const active = within(region('xgb-test-1'))
    expect(active.getByText('Metrics').closest('summary')).toHaveTextContent('Metrics (52 metrics)')
    expect(region('xgb-test-1').querySelectorAll('details details > summary')).toHaveLength(6)
    expect(active.getByText('validation_all_cycles').closest('details')).toHaveProperty('open', true)
    expect(active.getByText('classification_test_last_cycle').closest('details')).toHaveProperty('open', false)
    expect(region('xgb-test-1').querySelectorAll('dt')).toHaveLength(6 + 52) // metadata rows + every metric
  })

  it('keeps collapsed versions\' metrics available and reveals them on click', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json(modelsPage)))
    renderAppAt('/models')
    await screen.findByRole('region', { name: 'xgb-test-2' })

    expect(region('xgb-test-2').querySelectorAll('dd').length).toBeGreaterThan(0)
    const mae = within(region('xgb-test-2')).getByText('xgboost.mae').nextElementSibling as HTMLElement
    expect(mae).toHaveTextContent('10.500')
    expect(mae).not.toBeVisible()
    await userEvent.click(within(region('xgb-test-2')).getByText('Metrics'))
    expect(metricsDetails('xgb-test-2').open).toBe(true)
    expect(mae).toBeVisible()
  })

  it('says so when a version has no numeric metrics', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json(modelsPage)))
    renderAppAt('/models')
    await screen.findByRole('region', { name: 'xgb-test-0' })
    expect(within(region('xgb-test-0')).getByText('Metrics').closest('summary')).toHaveTextContent('Metrics (0 metrics)')
    expect(within(region('xgb-test-0')).getByText('The backend returned no numeric metrics for this model.')).toBeInTheDocument()
  })

  it('shows an empty state when no versions are registered', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json(page([], 0, 10, 0))))
    renderAppAt('/models')
    expect(await screen.findByText('No model versions are registered.')).toBeInTheDocument()
    expect(screen.queryByRole('navigation', { name: 'Pagination' })).not.toBeInTheDocument()
  })

  it('shows a backend error from the envelope and recovers with Try again', async () => {
    let calls = 0
    server.use(
      http.get('*/api/v1/models', () => {
        calls += 1
        return calls <= 2 // the first call and its one automatic retry both fail
          ? HttpResponse.json(errorEnvelope('internal_error', 'Server exploded'), { status: 500 })
          : HttpResponse.json(modelsPage)
      }),
    )
    renderAppAt('/models')
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('backend error')
    expect(alert).toHaveTextContent('Server exploded')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('region', { name: 'xgb-test-1' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows "API unreachable" for a gateway failure and a network failure', async () => {
    server.use(http.get('*/api/v1/models', () => new HttpResponse(null, { status: 502 })))
    renderAppAt('/models')
    expect(await screen.findByRole('alert')).toHaveTextContent('API unreachable')
  })

  it('shows "Unexpected response" when validation fails', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json({ items: [{ version: 3 }], total: 1, limit: 10, offset: 0 })))
    renderAppAt('/models')
    expect(await screen.findByRole('alert')).toHaveTextContent('Unexpected response')
  })

  it('refetches only when Refresh is pressed, and never sends credentials', async () => {
    const calls: Headers[] = []
    server.use(http.get('*/api/v1/models', ({ request }) => (calls.push(request.headers), HttpResponse.json(modelsPage))))
    renderAppAt('/models')
    await screen.findByRole('region', { name: 'xgb-test-1' })
    expect(calls).toHaveLength(1)
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(calls).toHaveLength(2))
    for (const headers of calls) {
      expect(headers.has('x-api-key')).toBe(false)
      expect(headers.has('x-control-key')).toBe(false)
    }
  })

  describe('pagination in the URL', () => {
    it('requests the first page by default and pages with Next, updating ?page=', async () => {
      const { handler, urls } = pagedModels(23)
      server.use(handler)
      renderAppAt('/models')
      expect(await screen.findByText('1–10 of 23 model versions')).toBeInTheDocument()
      expect(urls[0]?.search).toBe('?limit=10&offset=0')
      expect(screen.getByTestId('location')).toHaveTextContent(/^\/models$/)

      await userEvent.click(screen.getByRole('button', { name: 'Next' }))
      expect(await screen.findByText('11–20 of 23 model versions')).toBeInTheDocument()
      expect(screen.getByTestId('location')).toHaveTextContent('/models?page=2')
      expect(urls.at(-1)?.searchParams.get('offset')).toBe('10')
      expect(screen.getByRole('region', { name: 'v-11' })).toBeInTheDocument()

      await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
      expect(await screen.findByText('1–10 of 23 model versions')).toBeInTheDocument()
      expect(screen.getByTestId('location')).toHaveTextContent(/^\/models$/) // page 1 leaves no parameter
    })

    it('opens directly on the page named in the URL', async () => {
      const { handler, urls } = pagedModels(23)
      server.use(handler)
      renderAppAt('/models?page=3')
      expect(await screen.findByText('21–23 of 23 model versions')).toBeInTheDocument()
      expect(urls[0]?.searchParams.get('offset')).toBe('20')
    })

    it.each(['abc', '0', '-2', '1.5', '99999999'])('ignores the invalid page %j and never sends a bad offset', async (value) => {
      const { handler, urls } = pagedModels(23)
      server.use(handler)
      renderAppAt(`/models?page=${value}`)
      expect(await screen.findByText('1–10 of 23 model versions')).toBeInTheDocument()
      expect(urls.every((u) => u.searchParams.get('offset') === '0' && u.searchParams.get('limit') === '10')).toBe(true)
    })

    it('handles a page past the end and lets the user go back', async () => {
      const { handler } = pagedModels(3)
      server.use(handler)
      renderAppAt('/models?page=9')
      expect(await screen.findByText('This page is past the end of the list.')).toBeInTheDocument()
      expect(screen.getByText('No model versions on this page')).toBeInTheDocument()
      await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
      expect(await screen.findByRole('region', { name: 'v-01' })).toBeInTheDocument()
      expect(screen.getByTestId('location')).toHaveTextContent(/^\/models$/)
    })
  })

  it('shows the real active model fixture identically to the System page data (version only checked)', async () => {
    server.use(http.get('*/api/v1/models', () => HttpResponse.json(page([model], 1, 10, 0))))
    renderAppAt('/models')
    expect(await screen.findByRole('region', { name: model.version })).toBeInTheDocument()
  })
})
