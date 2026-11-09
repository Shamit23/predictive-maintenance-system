// Normalising URL query parameters. Anything that is not a clean, known value is ignored, so a hand-edited URL can
// never make the app send a request the backend would reject (422).

/** A 1-based page number; anything that is not a plain positive integer (up to 6 digits) becomes page 1. */
export function parsePage(raw: string | null): number {
  return raw !== null && /^[1-9]\d{0,5}$/.test(raw) ? Number(raw) : 1
}

export function offsetForPage(page: number, pageSize: number): number {
  return (page - 1) * pageSize
}

/** The value if it is exactly one of `allowed`, otherwise undefined. */
export function parseEnum<T extends string>(raw: string | null, allowed: readonly T[]): T | undefined {
  return allowed.find((candidate) => candidate === raw)
}

/** A string of digits of at most `maxLength` characters, otherwise undefined. */
export function parseDigits(raw: string | null, maxLength: number): string | undefined {
  return raw !== null && raw.length > 0 && raw.length <= maxLength && /^\d+$/.test(raw) ? raw : undefined
}

/** A positive integer id (up to 9 digits), otherwise undefined. */
export function parseId(raw: string | null): number | undefined {
  return raw !== null && /^[1-9]\d{0,8}$/.test(raw) ? Number(raw) : undefined
}

/**
 * A copy of `previous` with `changes` applied: a defined value sets the parameter, `undefined` removes it. Changing a
 * filter must drop `page` (a new filter starts on page 1), which callers do by passing `page: undefined`.
 */
export function withParams(previous: URLSearchParams, changes: Record<string, string | undefined>): URLSearchParams {
  const next = new URLSearchParams(previous)
  for (const [key, value] of Object.entries(changes)) {
    if (value === undefined) next.delete(key)
    else next.set(key, value)
  }
  return next
}

/** The `page` parameter value for an offset: page 1 is the default and is represented by no parameter at all. */
export function pageParamForOffset(offset: number, pageSize: number): string | undefined {
  const page = Math.floor(offset / pageSize) + 1
  return page === 1 ? undefined : String(page)
}
