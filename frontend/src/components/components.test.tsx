import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { NetworkError } from '../api/errors'
import { PageHeader } from './PageHeader'
import { EmptyState, PageError, PageLoading } from './PageState'
import { Pagination } from './Pagination'
import { ResultsStatus } from './ResultsStatus'
import { AlertSeverityBadge, AlertStatusBadge, ModelStatusBadge, OriginBadge, RiskBadge } from './StatusBadges'

describe('Pagination', () => {
  const setup = (total: number, limit: number, offset: number, noun?: string, plural?: string) => {
    const onOffsetChange = vi.fn()
    render(<Pagination total={total} limit={limit} offset={offset} noun={noun} plural={plural} onOffsetChange={onOffsetChange} />)
    return onOffsetChange
  }

  it('renders nothing for an empty list', () => {
    setup(0, 10, 0)
    expect(screen.queryByRole('navigation', { name: 'Pagination' })).not.toBeInTheDocument()
  })

  it('shows the range, the page number and a disabled Previous on the first page', () => {
    setup(23, 10, 0, 'engine')
    expect(screen.getByRole('navigation', { name: 'Pagination' })).toBeInTheDocument()
    expect(screen.getByText('1–10 of 23 engines')).toBeInTheDocument()
    expect(screen.getByText('Page 1 of 3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()
  })

  it('shows a partial last page with Next disabled', () => {
    setup(23, 10, 20)
    expect(screen.getByText('21–23 of 23 items')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled()
  })

  it('reports the next and previous offsets', async () => {
    const onOffsetChange = setup(23, 10, 10)
    await userEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(onOffsetChange).toHaveBeenLastCalledWith(20)
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(onOffsetChange).toHaveBeenLastCalledWith(0)
  })

  it('from an out-of-range page, Previous goes to the last page that exists', async () => {
    const onOffsetChange = setup(23, 10, 80, 'engine')
    expect(screen.getByText('No engines on this page')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(onOffsetChange).toHaveBeenCalledWith(20)
  })

  it('uses the singular for exactly one item and a single number instead of "1–1"', () => {
    setup(1, 10, 0, 'engine')
    expect(screen.getByText('1 of 1 engine')).toBeInTheDocument()
    expect(screen.queryByText(/1–1/)).not.toBeInTheDocument()
  })

  it('uses the plural for several items and for a last page holding a single one', () => {
    setup(51, 25, 50, 'engine')
    expect(screen.getByText('51 of 51 engines')).toBeInTheDocument()
  })

  it('supports an irregular plural', () => {
    setup(23, 10, 0, 'index', 'indices')
    expect(screen.getByText('1–10 of 23 indices')).toBeInTheDocument()
  })

  it('falls back to the generic noun', () => {
    setup(23, 10, 0)
    expect(screen.getByText('1–10 of 23 items')).toBeInTheDocument()
  })

  it('buttons are real buttons reachable by keyboard', async () => {
    const onOffsetChange = setup(23, 10, 0)
    await userEvent.tab()
    // Previous is disabled and skipped; the first focusable control is Next.
    expect(screen.getByRole('button', { name: 'Next' })).toHaveFocus()
    await userEvent.keyboard('{Enter}')
    expect(onOffsetChange).toHaveBeenCalledWith(10)
  })
})

describe('ResultsStatus', () => {
  const live = (text: string) => screen.getByText(text)

  it('announces the result count politely and as a whole message', () => {
    render(<ResultsStatus stale={false} page={{ total: 60, offset: 0, limit: 25 }} noun="engine" />)
    const region = live('60 engines found. Showing 1 to 25.')
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(region).toHaveAttribute('aria-atomic', 'true')
    expect(region).toHaveClass('sr-only') // visually hidden: sighted users read the pager
  })

  it('describes later pages, the singular case, an empty result and a page past the end', () => {
    const { rerender } = render(<ResultsStatus stale={false} page={{ total: 60, offset: 50, limit: 25 }} noun="engine" />)
    expect(live('60 engines found. Showing 51 to 60.')).toBeInTheDocument()
    rerender(<ResultsStatus stale={false} page={{ total: 1, offset: 0, limit: 25 }} noun="engine" />)
    expect(live('1 engine found. Showing 1 to 1.')).toBeInTheDocument()
    rerender(<ResultsStatus stale={false} page={{ total: 0, offset: 0, limit: 25 }} noun="engine" />)
    expect(live('No engines found.')).toBeInTheDocument()
    rerender(<ResultsStatus stale={false} page={{ total: 3, offset: 75, limit: 25 }} noun="engine" />)
    expect(live('No engines on this page.')).toBeInTheDocument()
  })

  it('uses an irregular plural when given', () => {
    render(<ResultsStatus stale={false} page={{ total: 2, offset: 0, limit: 10 }} noun="index" plural="indices" />)
    expect(live('2 indices found. Showing 1 to 2.')).toBeInTheDocument()
  })

  it('while stale it announces "Updating results…", shows a visible "Updating…" and hides the old summary', () => {
    render(<ResultsStatus stale page={{ total: 60, offset: 0, limit: 25 }} noun="engine" />)
    expect(live('Updating results…')).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByText('Updating…')).toBeVisible()
    expect(screen.queryByText(/60 engines found/)).not.toBeInTheDocument()
  })

  it('keeps an empty live region before any results exist, so the first announcement is not lost', () => {
    const { container } = render(<ResultsStatus stale={false} noun="engine" />)
    const region = container.querySelector('[aria-live="polite"]')
    expect(region).toBeInTheDocument()
    expect(region).toBeEmptyDOMElement()
    expect(screen.queryByText('Updating…')).not.toBeInTheDocument()
  })
})

describe('StatusBadges', () => {
  it('labels every risk value and a missing prediction', () => {
    render(
      <>
        <RiskBadge risk="normal" />
        <RiskBadge risk="warning" />
        <RiskBadge risk="critical" />
        <RiskBadge risk={null} />
      </>,
    )
    for (const text of ['Normal', 'Warning', 'Critical', 'No prediction']) expect(screen.getByText(text)).toBeInTheDocument()
  })

  it('labels alert severity and status', () => {
    render(
      <>
        <AlertSeverityBadge severity="critical" />
        <AlertStatusBadge status="open" />
        <AlertStatusBadge status="acknowledged" />
        <AlertStatusBadge status="resolved" />
      </>,
    )
    for (const text of ['Critical', 'Open', 'Acknowledged', 'Resolved']) expect(screen.getByText(text)).toBeInTheDocument()
  })

  it('never calls ordinary ingestion "real"', () => {
    render(
      <>
        <OriginBadge origin="api" />
        <OriginBadge origin="simulation" />
      </>,
    )
    expect(screen.getByText('Ordinary ingestion')).toBeInTheDocument()
    expect(screen.getByText('Simulated')).toBeInTheDocument()
    expect(screen.queryByText(/real/i)).not.toBeInTheDocument()
  })

  it('labels model statuses', () => {
    render(
      <>
        <ModelStatusBadge status="active" />
        <ModelStatusBadge status="candidate" />
        <ModelStatusBadge status="archived" />
      </>,
    )
    for (const text of ['Active', 'Candidate', 'Archived']) expect(screen.getByText(text)).toBeInTheDocument()
  })
})

describe('page state components', () => {
  it('PageLoading announces itself as a status', () => {
    render(<PageLoading what="engines" />)
    expect(screen.getByRole('status')).toHaveTextContent('Loading engines…')
  })

  it('PageError shows the error as an alert and retries on request', async () => {
    const onRetry = vi.fn()
    render(<PageError error={new NetworkError()} onRetry={onRetry} />)
    expect(screen.getByRole('alert')).toHaveTextContent('API unreachable')
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('EmptyState shows its title and optional detail', () => {
    render(<EmptyState title="Nothing here">Try a different filter.</EmptyState>)
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
    expect(screen.getByText('Try a different filter.')).toBeInTheDocument()
  })

  it('PageHeader has a level-1 heading and a Refresh button that is disabled while refreshing', async () => {
    const onRefresh = vi.fn()
    const { rerender } = render(<PageHeader title="Models" onRefresh={onRefresh} isRefreshing={false} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Models' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(onRefresh).toHaveBeenCalledOnce()
    rerender(<PageHeader title="Models" onRefresh={onRefresh} isRefreshing />)
    expect(screen.getByRole('button', { name: 'Refreshing…' })).toBeDisabled()
  })
})
