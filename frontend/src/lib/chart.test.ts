import { describe, expect, it } from 'vitest'
import {
  CHART_MARGIN,
  CHART_SIZE,
  LINE_DASHES,
  MARKER_SHAPES,
  buildChartGeometry,
  describeChart,
  formatTick,
  lineDash,
  markerShapeName,
  niceStep,
  ticksBetween,
} from './chart'
import type { ChartPrediction } from './chart'

const point = (id: number, cycle: number, rul: number, version = 'v1', risk: ChartPrediction['risk_category'] = 'normal'): ChartPrediction => ({
  id,
  cycle,
  predicted_rul: rul,
  risk_category: risk,
  model_version: version,
})

/** 200 consecutive cycles (163..362) falling from 120 to about 0.5 RUL. */
const longHistory = Array.from({ length: 200 }, (_, i) => point(i + 1, 163 + i, 120 - i * 0.6))

const plotLeft = CHART_MARGIN.left
const plotRight = CHART_SIZE.width - CHART_MARGIN.right
const plotTop = CHART_MARGIN.top
const plotBottom = CHART_SIZE.height - CHART_MARGIN.bottom

describe('buildChartGeometry: no data', () => {
  it('returns null for zero predictions', () => {
    expect(buildChartGeometry([])).toBeNull()
  })

  it('returns null when nothing is finite, rather than inventing a chart', () => {
    expect(buildChartGeometry([point(1, Number.NaN, 5), point(2, 3, Number.POSITIVE_INFINITY)])).toBeNull()
  })

  it('drops only non-finite predictions and reports how many', () => {
    const geometry = buildChartGeometry([point(1, 1, 50), point(2, Number.NaN, 5), point(3, 3, 40)])
    expect(geometry?.points.map((p) => p.id)).toEqual([1, 3])
    expect(geometry?.skipped).toBe(1)
  })
})

describe('buildChartGeometry: one point', () => {
  const geometry = buildChartGeometry([point(7, 42, 37.5)])

  it('plots it once, centred horizontally in a symmetric window, with a single labelled cycle tick', () => {
    expect(geometry?.points).toHaveLength(1)
    expect(geometry?.xDomain).toEqual([41, 43])
    expect(geometry?.points[0]?.x).toBe((plotLeft + plotRight) / 2)
    expect(geometry?.xTicks).toEqual([{ value: 42, position: (plotLeft + plotRight) / 2 }])
  })

  it('has a marker but no line segment', () => {
    expect(geometry?.series).toHaveLength(1)
    expect(geometry?.series[0]?.path).toMatch(/^M [\d.]+ [\d.]+$/)
    expect(geometry?.series[0]?.pointCount).toBe(1)
  })

  it('scales Y from zero with nice bounds and puts the point inside the plot', () => {
    expect(geometry?.yDomain).toEqual([0, 40])
    expect(geometry?.points[0]?.y).toBeGreaterThanOrEqual(plotTop)
    expect(geometry?.points[0]?.y).toBeLessThanOrEqual(plotBottom)
  })
})

describe('buildChartGeometry: many points', () => {
  const geometry = buildChartGeometry(longHistory)

  it('keeps all 200 points, in cycle order, spanning the whole plot width', () => {
    expect(geometry?.points).toHaveLength(200)
    const cycles = geometry?.points.map((p) => p.cycle) as number[]
    expect(cycles).toEqual([...cycles].sort((a, b) => a - b))
    expect(geometry?.xDomain).toEqual([163, 362])
    expect(geometry?.points[0]?.x).toBe(plotLeft)
    expect(geometry?.points[199]?.x).toBe(plotRight)
  })

  it('produces only finite coordinates inside the plot area', () => {
    for (const p of geometry?.points ?? []) {
      expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true)
      expect(p.x).toBeGreaterThanOrEqual(plotLeft)
      expect(p.x).toBeLessThanOrEqual(plotRight)
      expect(p.y).toBeGreaterThanOrEqual(plotTop)
      expect(p.y).toBeLessThanOrEqual(plotBottom)
    }
    expect(JSON.stringify(geometry)).not.toMatch(/NaN|Infinity/)
  })

  it('draws one connected line through all points', () => {
    const path = geometry?.series[0]?.path ?? ''
    expect(path.match(/M/g)).toHaveLength(1)
    expect(path.match(/L/g)).toHaveLength(199)
  })

  it('maps larger RUL higher on the plot (smaller y) and zero to the bottom edge', () => {
    const first = geometry?.points[0]
    const last = geometry?.points[199]
    expect((first?.y ?? 0) < (last?.y ?? 0)).toBe(true) // 120 RUL is above about 0.6 RUL
    expect(geometry?.yDomain[0]).toBe(0)
    expect(geometry?.yTicks[0]).toEqual({ value: 0, position: plotBottom })
  })

  it('has a few ticks, all inside the domains, increasing, without duplicates', () => {
    const xs = geometry?.xTicks.map((t) => t.value) as number[]
    expect(xs.length).toBeGreaterThan(2)
    expect(xs.length).toBeLessThanOrEqual(8)
    expect(xs.every((v) => v >= 163 && v <= 362)).toBe(true)
    expect(new Set(xs).size).toBe(xs.length)
    expect(xs).toEqual([...xs].sort((a, b) => a - b))
    const ys = geometry?.yTicks.map((t) => t.value) as number[]
    expect(ys[0]).toBe(0)
    expect(ys.at(-1)).toBe(geometry?.yDomain[1])
  })
})

describe('buildChartGeometry: ordering, duplicates and model versions', () => {
  it('orders unsorted input by cycle and breaks ties by prediction id', () => {
    const geometry = buildChartGeometry([point(9, 5, 10), point(2, 3, 20), point(8, 5, 11), point(1, 4, 15)])
    expect(geometry?.points.map((p) => [p.cycle, p.id])).toEqual([[3, 2], [4, 1], [5, 8], [5, 9]])
  })

  it('keeps several predictions at the same cycle as separate points at the same x', () => {
    const geometry = buildChartGeometry([point(1, 10, 50, 'old'), point(2, 10, 44, 'new'), point(3, 11, 43, 'new')])
    expect(geometry?.points).toHaveLength(3)
    const atTen = geometry?.points.filter((p) => p.cycle === 10) ?? []
    expect(atTen).toHaveLength(2)
    expect(atTen[0]?.x).toBe(atTen[1]?.x)
    expect(atTen[0]?.y).not.toBe(atTen[1]?.y)
    expect(geometry?.duplicateCycles).toBe(1)
  })

  it('counts each duplicated cycle once, however many predictions it holds', () => {
    const geometry = buildChartGeometry([
      point(1, 1, 9, 'a'), point(2, 1, 8, 'b'), point(3, 1, 7, 'c'), point(4, 2, 6, 'a'), point(5, 2, 5, 'b'), point(6, 3, 4, 'a'),
    ])
    expect(geometry?.points).toHaveLength(6)
    expect(geometry?.duplicateCycles).toBe(2)
  })

  it('reports no duplicate cycles for a clean history', () => {
    expect(buildChartGeometry(longHistory)?.duplicateCycles).toBe(0)
  })

  it('gives each model version its own series, shape and line, in order of first appearance', () => {
    const geometry = buildChartGeometry([
      point(1, 1, 90, 'beta'), point(2, 2, 85, 'beta'), point(3, 2, 84, 'alpha'), point(4, 3, 80, 'alpha'),
    ])
    expect(geometry?.versions).toEqual(['beta', 'alpha'])
    expect(geometry?.series.map((s) => [s.version, s.shape, s.pointCount])).toEqual([['beta', 0, 2], ['alpha', 1, 2]])
    expect(geometry?.points.map((p) => [p.version, p.shape])).toEqual([['beta', 0], ['beta', 0], ['alpha', 1], ['alpha', 1]])
  })

  it('keeps shape indexes distinct beyond the number of marker shapes; the names then wrap', () => {
    const many = Array.from({ length: MARKER_SHAPES.length + 2 }, (_, i) => point(i + 1, i + 1, 50 - i, `v${i}`))
    const geometry = buildChartGeometry(many)
    expect(geometry?.series.map((s) => s.shape)).toEqual(many.map((_, i) => i))
    expect(markerShapeName(MARKER_SHAPES.length)).toBe(MARKER_SHAPES[0])
    expect(lineDash(LINE_DASHES.length + 1)).toBe(LINE_DASHES[1])
  })

  it('carries the API risk category on every point and invents none', () => {
    const geometry = buildChartGeometry([point(1, 1, 90, 'v', 'normal'), point(2, 2, 45, 'v', 'warning'), point(3, 3, 10, 'v', 'critical')])
    expect(geometry?.points.map((p) => p.risk)).toEqual(['normal', 'warning', 'critical'])
  })
})

describe('buildChartGeometry: edge values', () => {
  it('handles all-zero predictions with a valid positive-height axis and points on the baseline', () => {
    const geometry = buildChartGeometry([point(1, 1, 0), point(2, 2, 0)])
    expect(geometry?.yDomain[1]).toBeGreaterThan(geometry?.yDomain[0] ?? 0)
    expect(geometry?.points.every((p) => p.y === plotBottom)).toBe(true)
    expect(JSON.stringify(geometry)).not.toMatch(/NaN|Infinity/)
  })

  it('extends the axis downwards for a negative prediction instead of clipping it', () => {
    const geometry = buildChartGeometry([point(1, 1, 30), point(2, 2, -8)])
    expect(geometry?.yDomain[0]).toBeLessThan(0)
    expect(geometry?.points.every((p) => p.y >= plotTop && p.y <= plotBottom)).toBe(true)
    expect(geometry?.yTicks.some((t) => t.value === 0)).toBe(true)
  })

  it('handles a flat history and a very large range', () => {
    const flat = buildChartGeometry([point(1, 1, 50), point(2, 2, 50), point(3, 3, 50)])
    expect(new Set(flat?.points.map((p) => p.y)).size).toBe(1)
    const big = buildChartGeometry([point(1, 1, 1_000_000), point(2, 2, 3)])
    expect(big?.yDomain).toEqual([0, 1_000_000])
    expect(JSON.stringify(big)).not.toMatch(/NaN|Infinity/)
  })

  it('handles non-integer RUL and a big cycle gap', () => {
    const geometry = buildChartGeometry([point(1, 1, 0.37), point(2, 91_100, 0.12)])
    expect(geometry?.xDomain).toEqual([1, 91_100])
    expect(geometry?.yDomain[1]).toBeGreaterThan(0.37)
    expect(JSON.stringify(geometry)).not.toMatch(/NaN|Infinity/)
  })

  it('does not modify its input', () => {
    const input = [point(2, 5, 10), point(1, 3, 20)]
    const copy = JSON.stringify(input)
    buildChartGeometry(input)
    expect(JSON.stringify(input)).toBe(copy)
  })
})

describe('tick helpers', () => {
  it.each([
    [0.07, 0.1], [1, 1], [1.1, 2], [2, 2], [3, 5], [5, 5], [6, 10], [24, 50], [0, 1], [-4, 1], [Number.NaN, 1],
  ])('niceStep(%s) = %s', (raw, expected) => {
    expect(niceStep(raw)).toBe(expected)
  })

  it('ticksBetween returns the multiples of the step inside the range, without floating-point noise', () => {
    expect(ticksBetween(0, 100, 25)).toEqual([0, 25, 50, 75, 100])
    expect(ticksBetween(3, 17, 5)).toEqual([5, 10, 15])
    expect(ticksBetween(0, 1, 0.1)).toHaveLength(11)
    expect(ticksBetween(0, 1, 0.1)[3]).toBe(0.3)
  })

  it('formatTick shows whole numbers plainly and trims decimals', () => {
    expect(formatTick(50)).toBe('50')
    expect(formatTick(0.5)).toBe('0.5')
    expect(formatTick(0.30000000000000004)).toBe('0.3')
    expect(formatTick(-20)).toBe('-20')
  })
})

describe('describeChart (the accessible description)', () => {
  const describeOf = (items: ChartPrediction[]) => describeChart(buildChartGeometry(items) as NonNullable<ReturnType<typeof buildChartGeometry>>)

  it('summarises a history: count, cycle range, latest and lowest predicted RUL, model version', () => {
    const text = describeOf(longHistory)
    expect(text).toContain('200 predictions from cycle 163 to cycle 362')
    expect(text).toContain('Latest point: cycle 362, predicted RUL 0.6 cycles.')
    expect(text).toContain('Lowest predicted RUL: 0.6 cycles at cycle 362.')
    expect(text).toContain('Model version: v1.')
  })

  it('describes a single prediction in the singular without a range or a "lowest" claim', () => {
    const text = describeOf([point(1, 12, 33.3)])
    expect(text).toContain('1 prediction at cycle 12')
    expect(text).not.toMatch(/Lowest|from cycle/)
  })

  it('states several model versions and shared cycles', () => {
    const text = describeOf([point(1, 5, 50, 'old'), point(2, 5, 48, 'new'), point(3, 6, 47, 'new')])
    expect(text).toContain('Predictions come from 2 model versions: old, new.')
    expect(text).toContain('1 cycle has more than one prediction; all are plotted.')
    expect(describeOf([point(1, 1, 5, 'a'), point(2, 1, 4, 'b'), point(3, 2, 3, 'a'), point(4, 2, 2, 'b')])).toContain('2 cycles have more than one prediction')
  })

  it('talks about predictions only: never "actual" remaining life or thresholds', () => {
    const text = describeOf(longHistory)
    expect(text).toMatch(/Predicted remaining useful life/)
    expect(text).not.toMatch(/actual|true RUL|threshold|failure at/i)
  })
})
