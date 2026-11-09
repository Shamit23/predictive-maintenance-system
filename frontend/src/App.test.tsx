import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { HttpResponse, http } from 'msw'
import { okHandlers, page } from './test/fixtures'
import { renderAppAt } from './test/render'
import { server } from './test/server'

const nav = () => within(screen.getByRole('navigation', { name: 'Main' }))

describe('navigation shell', () => {
  it('has a labelled main navigation with a link for every page that exists and no dead links', async () => {
    server.use(...okHandlers)
    renderAppAt('/')
    await screen.findByText('API healthy')
    const links = nav().getAllByRole('link')
    // Only pages that exist are linked: the engine detail and simulation pages are not part of the app yet.
    expect(links.map((l) => [l.textContent, l.getAttribute('href')])).toEqual([
      ['System', '/'],
      ['Engines', '/engines'],
      ['Alerts', '/alerts'],
      ['Models', '/models'],
    ])
  })

  it('marks only the current page with aria-current, including after navigating', async () => {
    server.use(...okHandlers)
    renderAppAt('/')
    await screen.findByText('API healthy')
    expect(nav().getByRole('link', { name: 'System' })).toHaveAttribute('aria-current', 'page')
    expect(nav().getByRole('link', { name: 'Models' })).not.toHaveAttribute('aria-current')

    await userEvent.click(nav().getByRole('link', { name: 'Models' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Models' })).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent('/models')
    expect(nav().getByRole('link', { name: 'Models' })).toHaveAttribute('aria-current', 'page')
    // "/" must not stay current just because every path starts with it.
    expect(nav().getByRole('link', { name: 'System' })).not.toHaveAttribute('aria-current')
  })

  it('navigates back to the System page', async () => {
    server.use(...okHandlers)
    renderAppAt('/models')
    await screen.findByRole('heading', { level: 1, name: 'Models' })
    await userEvent.click(nav().getByRole('link', { name: 'System' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'System status' })).toBeInTheDocument()
  })

  it('keeps the navigation visible on an unknown route and offers the way back', () => {
    renderAppAt('/nope')
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(nav().getAllByRole('link')).toHaveLength(4)
    expect(screen.getByRole('link', { name: 'Back to System status' })).toHaveAttribute('href', '/')
  })

  it('shows the main landmark and the app title', () => {
    renderAppAt('/nope')
    expect(screen.getByRole('main')).toBeInTheDocument()
    expect(screen.getByText('Predictive Maintenance')).toBeInTheDocument()
  })

  it('links are keyboard reachable in order, after the skip link', async () => {
    renderAppAt('/nope')
    await userEvent.tab()
    expect(screen.getByRole('link', { name: 'Skip to main content' })).toHaveFocus()
    await userEvent.tab()
    expect(nav().getByRole('link', { name: 'System' })).toHaveFocus()
    await userEvent.tab()
    expect(nav().getByRole('link', { name: 'Engines' })).toHaveFocus()
    await userEvent.tab()
    expect(nav().getByRole('link', { name: 'Alerts' })).toHaveFocus()
    await userEvent.tab()
    expect(nav().getByRole('link', { name: 'Models' })).toHaveFocus()
  })

  it.each([
    ['/engines', 'Engines'],
    ['/alerts', 'Alerts'],
  ])('routes %s to its page and marks it current', async (path, title) => {
    server.use(
      http.get('*/api/v1/engines', () => HttpResponse.json(page([], 0, 25, 0))),
      http.get('*/api/v1/alerts', () => HttpResponse.json(page([], 0, 25, 0))),
    )
    renderAppAt(path)
    expect(await screen.findByRole('heading', { level: 1, name: title })).toBeInTheDocument()
    expect(nav().getByRole('link', { name: title })).toHaveAttribute('aria-current', 'page')
    expect(nav().getByRole('link', { name: 'System' })).not.toHaveAttribute('aria-current')
  })

  it('offers a skip link as the first tab stop that moves focus to the main content', async () => {
    renderAppAt('/nope')
    await userEvent.tab()
    const skip = screen.getByRole('link', { name: 'Skip to main content' })
    expect(skip).toHaveFocus()
    expect(skip).toHaveAttribute('href', '#main-content')
    await userEvent.keyboard('{Enter}')
    expect(screen.getByRole('main')).toHaveFocus()
    expect(screen.getByTestId('location')).not.toHaveTextContent('#') // no hash is added to the URL
  })

  it('makes the main landmark a programmatic focus target', () => {
    renderAppAt('/nope')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main-content')
    expect(screen.getByRole('main')).toHaveAttribute('tabindex', '-1')
  })
})

describe('document titles and route announcements', () => {
  const lists = [
    http.get('*/api/v1/engines', () => HttpResponse.json(page([], 0, 25, 0))),
    http.get('*/api/v1/alerts', () => HttpResponse.json(page([], 0, 25, 0))),
  ]
  const announcer = () => screen.getByTestId('route-announcer')

  it.each([
    ['/', 'System status'],
    ['/engines', 'Engines'],
    ['/alerts', 'Alerts'],
    ['/models', 'Models'],
    ['/nope', 'Page not found'],
  ])('gives %s the title "%s"', async (path, title) => {
    server.use(...lists, ...okHandlers)
    renderAppAt(path)
    await waitFor(() => expect(document.title).toBe(`${title} | Predictive Maintenance`))
  })

  it('announces a polite live region for navigation', () => {
    renderAppAt('/nope')
    expect(announcer()).toHaveAttribute('aria-live', 'polite')
    expect(announcer()).toHaveAttribute('aria-atomic', 'true')
  })

  it('stays quiet and leaves focus alone on first load', async () => {
    server.use(...lists, ...okHandlers)
    renderAppAt('/')
    await screen.findByText('API healthy')
    expect(screen.getByRole('main')).not.toHaveFocus()
    expect(announcer()).toBeEmptyDOMElement()
  })

  it('moves focus to the main content and announces the new page after navigating', async () => {
    server.use(...lists, ...okHandlers)
    renderAppAt('/')
    await screen.findByText('API healthy')
    await userEvent.click(nav().getByRole('link', { name: 'Alerts' }))
    expect(screen.getByRole('main')).toHaveFocus()
    expect(announcer()).toHaveTextContent('Navigated to Alerts')
    expect(document.title).toBe('Alerts | Predictive Maintenance')
    await userEvent.click(nav().getByRole('link', { name: 'Models' }))
    expect(announcer()).toHaveTextContent('Navigated to Models')
    expect(screen.getByRole('main')).toHaveFocus()
  })

  it('does not steal focus or announce when only filters or the page change', async () => {
    server.use(...lists, ...okHandlers)
    renderAppAt('/alerts')
    await screen.findByText('No unresolved alerts.')
    const severity = screen.getByLabelText('Severity')
    severity.focus()
    await userEvent.selectOptions(severity, 'critical')
    expect(screen.getByTestId('location')).toHaveTextContent('/alerts?severity=critical')
    expect(screen.getByRole('main')).not.toHaveFocus()
    expect(announcer()).toBeEmptyDOMElement()
  })
})

describe('unfinished routes', () => {
  it('does not route the simulation history page (deferred)', () => {
    renderAppAt('/simulation')
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })

  it('routes engine detail pages, but not deeper or unknown paths under /engines', () => {
    server.use(http.get('*/api/v1/engines/24', () => HttpResponse.json({ error: { code: 'not_found', message: 'x', details: {} } }, { status: 404 })))
    renderAppAt('/engines/24')
    expect(screen.queryByRole('heading', { name: 'Page not found' })).not.toBeInTheDocument() // the detail route handles it
    renderAppAt('/engines/24/extra')
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})
