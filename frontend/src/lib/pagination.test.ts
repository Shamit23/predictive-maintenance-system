import { describe, expect, it } from 'vitest'
import { pageInfo } from './pagination'

describe('pageInfo', () => {
  it('describes the first page of several', () => {
    expect(pageInfo(23, 10, 0)).toEqual({
      page: 1, pages: 3, from: 1, to: 10, hasPrev: false, hasNext: true, lastOffset: 20, outOfRange: false,
    })
  })

  it('describes a middle page', () => {
    expect(pageInfo(23, 10, 10)).toMatchObject({ page: 2, from: 11, to: 20, hasPrev: true, hasNext: true })
  })

  it('describes a partial last page', () => {
    expect(pageInfo(23, 10, 20)).toMatchObject({ page: 3, from: 21, to: 23, hasPrev: true, hasNext: false })
  })

  it('has no next page when the total is an exact multiple of the page size', () => {
    expect(pageInfo(20, 10, 10)).toMatchObject({ pages: 2, from: 11, to: 20, hasNext: false })
  })

  it('handles an empty list as a single empty page', () => {
    expect(pageInfo(0, 10, 0)).toEqual({
      page: 1, pages: 1, from: 0, to: 0, hasPrev: false, hasNext: false, lastOffset: 0, outOfRange: false,
    })
  })

  it('flags an offset past the end, with no items shown and a way back', () => {
    expect(pageInfo(3, 10, 80)).toMatchObject({ outOfRange: true, from: 0, to: 0, hasPrev: true, hasNext: false, lastOffset: 0 })
  })

  it('treats an offset exactly at the total as out of range', () => {
    expect(pageInfo(20, 10, 20)).toMatchObject({ outOfRange: true, from: 0 })
  })
})
