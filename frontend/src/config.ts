/** Polling cadence for the System page, in milliseconds. Polling pauses while the tab is hidden (TanStack default). */
export const POLL_MS = {
  health: 15_000,
  ready: 15_000,
  simulation: 5_000,
  model: 60_000,
} as const

/** List and detail pages do not poll; data counts as fresh for this long before a revisit refetches it. */
export const LIST_STALE_MS = 15_000

/** How long fetched data counts as fresh, so remounts and window focus do not refetch needlessly. */
export const STALE_MS = 4_000
