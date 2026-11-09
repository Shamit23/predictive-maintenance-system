export interface PageInfo {
  /** 1-based page number implied by the offset. */
  page: number
  pages: number
  /** 1-based index of the first item shown, or 0 when the offset is past the end. */
  from: number
  to: number
  hasPrev: boolean
  hasNext: boolean
  /** Offset of the last page that exists (0 when there is only one page). */
  lastOffset: number
  /** True when the offset points past the data (the list shrank or the URL was edited). */
  outOfRange: boolean
}

/** Derive what a pager needs from an API page's `total`, the requested `limit` and the `offset`. */
export function pageInfo(total: number, limit: number, offset: number): PageInfo {
  const pages = Math.max(1, Math.ceil(total / limit))
  const outOfRange = total > 0 && offset >= total
  const inRange = total > 0 && offset < total
  return {
    page: Math.floor(offset / limit) + 1,
    pages,
    from: inRange ? offset + 1 : 0,
    to: inRange ? Math.min(offset + limit, total) : 0,
    hasPrev: offset > 0,
    hasNext: offset + limit < total,
    lastOffset: (pages - 1) * limit,
    outOfRange,
  }
}
