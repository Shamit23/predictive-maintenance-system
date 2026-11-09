import { QueryClient } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter, useLocation } from 'react-router'
import type { InitialEntry } from 'react-router'
import App from '../App'

/** Shows the router's current path and query string so tests can assert URL state (filters, page). */
function LocationProbe() {
  const location = useLocation()
  return (
    <div data-testid="location">
      {location.pathname}
      {location.search}
    </div>
  )
}

/**
 * Render the whole app at a route: a path string, or a location object when the test needs router history state
 * (for example `{ pathname: '/engines/24', state: { from: 'engines', search: '?risk=warning' } }`).
 * The hooks' own retry policy stays active; only the retry delay is removed.
 * Requests that no test handler covers fail the test (see src/test/setup.ts).
 */
export function renderAppAt(path: InitialEntry = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } })
  const utils = render(
    <MemoryRouter initialEntries={[path]}>
      <App client={client} />
      <LocationProbe />
    </MemoryRouter>,
  )
  return { client, ...utils }
}
