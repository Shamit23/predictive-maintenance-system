import type { Prediction, Risk } from '../api/schemas'
import { formatRul, pluralize } from './format'

// Pure geometry for the RUL history chart: no DOM, no React. The component draws exactly what this returns.

/** SVG user-space size. The chart scales with the container through a viewBox. */
export const CHART_SIZE = { width: 640, height: 320 } as const
export const CHART_MARGIN = { top: 16, right: 24, bottom: 52, left: 60 } as const

/** Marker shape per model version (by order of first appearance); repeats after this many versions. */
export const MARKER_SHAPES = ['circle', 'square', 'triangle', 'diamond', 'inverted triangle'] as const
export type MarkerShape = (typeof MARKER_SHAPES)[number]
/** Line pattern per model version (SVG `stroke-dasharray`; empty = solid). */
export const LINE_DASHES = ['', '7 4', '2 3', '9 3 2 3', '1 5'] as const

export type ChartPrediction = Pick<Prediction, 'id' | 'cycle' | 'predicted_rul' | 'risk_category' | 'model_version'>

export interface PlottedPoint {
  id: number
  cycle: number
  rul: number
  risk: Risk
  version: string
  /** Index into MARKER_SHAPES / LINE_DASHES (taken modulo their length). */
  shape: number
  x: number
  y: number
}

export interface Series {
  version: string
  shape: number
  /** SVG path: one move-to, then a line-to per further point (a lone point has no line segment). */
  path: string
  pointCount: number
}

export interface Tick {
  value: number
  /** x (for cycle ticks) or y (for RUL ticks) in SVG user space. */
  position: number
}

export interface ChartGeometry {
  width: number
  height: number
  plot: { left: number; top: number; right: number; bottom: number }
  xDomain: [number, number]
  yDomain: [number, number]
  /** Every plottable prediction, in cycle order (ties by id). Nothing is merged or dropped. */
  points: PlottedPoint[]
  series: Series[]
  /** Model versions in order of first appearance. */
  versions: string[]
  xTicks: Tick[]
  yTicks: Tick[]
  /** Predictions that could not be plotted (a non-finite cycle or RUL). The API schema should make this 0. */
  skipped: number
  /** How many distinct cycles hold more than one prediction (only possible across model versions). */
  duplicateCycles: number
}

const round2 = (n: number) => Math.round(n * 100) / 100
const clean = (n: number) => Number(n.toFixed(10)) // removes floating-point noise such as 0.30000000000000004

/** A "nice" step (1, 2, 5 times a power of ten) at or above `raw`. */
export function niceStep(raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0) return 1
  const exponent = Math.floor(Math.log10(raw))
  const base = 10 ** exponent
  const fraction = raw / base
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10
  return nice * base
}

/** Multiples of `step` inside [min, max]. */
export function ticksBetween(min: number, max: number, step: number): number[] {
  const out: number[] = []
  const first = Math.ceil(min / step - 1e-9) * step
  for (let value = first, guard = 0; value <= max + step * 1e-9 && guard < 200; value += step, guard++) out.push(clean(value))
  return out
}

/** Tick label: whole numbers as they are, others with at most two decimals. */
export function formatTick(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}

export function markerShapeName(shape: number): MarkerShape {
  return MARKER_SHAPES[shape % MARKER_SHAPES.length] as MarkerShape
}

export function lineDash(shape: number): string {
  return LINE_DASHES[shape % LINE_DASHES.length] as string
}

/**
 * Lay out predictions for plotting. Returns `null` when there is nothing to plot (no points, or none finite).
 *
 * - Points are sorted by cycle, ties by prediction id, and every one is kept: several predictions at one cycle (from
 *   different model versions) stay separate points.
 * - X is the cycle (a single cycle is centred in a width-2 window); Y is predicted RUL, always including zero, with
 *   "nice" bounds. Negative predictions, should the model produce them, extend the axis downwards.
 * - One series per model version, in order of first appearance, so a version is identifiable by shape and line pattern.
 */
export function buildChartGeometry(predictions: readonly ChartPrediction[]): ChartGeometry | null {
  const usable = predictions.filter((p) => Number.isFinite(p.cycle) && Number.isFinite(p.predicted_rul))
  const skipped = predictions.length - usable.length
  if (usable.length === 0) return null

  const sorted = [...usable].sort((a, b) => a.cycle - b.cycle || a.id - b.id)
  const versions = [...new Set(sorted.map((p) => p.model_version))]

  const { width, height } = CHART_SIZE
  const plot = {
    left: CHART_MARGIN.left,
    top: CHART_MARGIN.top,
    right: width - CHART_MARGIN.right,
    bottom: height - CHART_MARGIN.bottom,
  }
  const plotWidth = plot.right - plot.left
  const plotHeight = plot.bottom - plot.top

  // X domain: the cycles present; a single cycle gets a symmetric window so it sits mid-plot.
  const minCycle = sorted[0]?.cycle as number
  const maxCycle = sorted[sorted.length - 1]?.cycle as number
  const xDomain: [number, number] = minCycle === maxCycle ? [minCycle - 1, maxCycle + 1] : [minCycle, maxCycle]
  const xStep = Math.max(1, niceStep((xDomain[1] - xDomain[0]) / 6))
  const xValues = minCycle === maxCycle ? [minCycle] : ticksBetween(xDomain[0], xDomain[1], xStep)
  const xAt = (cycle: number) => round2(plot.left + ((cycle - xDomain[0]) / (xDomain[1] - xDomain[0])) * plotWidth)

  // Y domain: zero is always included, with nice bounds around the data.
  const ruls = sorted.map((p) => p.predicted_rul)
  const low = Math.min(0, ...ruls)
  const high = Math.max(0, ...ruls)
  const span = high === low ? 1 : high - low
  const yStep = niceStep(span / 5)
  const yMin = clean(Math.floor(low / yStep + 1e-9) * yStep)
  const yMax = clean(Math.max(Math.ceil(high / yStep - 1e-9) * yStep, yMin + yStep))
  const yDomain: [number, number] = [yMin, yMax]
  const yAt = (rul: number) => round2(plot.bottom - ((rul - yMin) / (yMax - yMin)) * plotHeight)

  const points: PlottedPoint[] = sorted.map((p) => ({
    id: p.id,
    cycle: p.cycle,
    rul: p.predicted_rul,
    risk: p.risk_category,
    version: p.model_version,
    shape: versions.indexOf(p.model_version),
    x: xAt(p.cycle),
    y: yAt(p.predicted_rul),
  }))

  const series: Series[] = versions.map((version, shape) => {
    const own = points.filter((p) => p.version === version)
    const path = own.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x} ${p.y}`).join(' ')
    return { version, shape, path, pointCount: own.length }
  })

  const perCycle = new Map<number, number>()
  for (const p of sorted) perCycle.set(p.cycle, (perCycle.get(p.cycle) ?? 0) + 1)
  const duplicateCycles = [...perCycle.values()].filter((count) => count > 1).length

  return {
    width,
    height,
    plot,
    xDomain,
    yDomain,
    points,
    series,
    versions,
    xTicks: xValues.map((value) => ({ value, position: xAt(value) })),
    yTicks: ticksBetween(yMin, yMax, yStep).map((value) => ({ value, position: yAt(value) })),
    skipped,
    duplicateCycles,
  }
}

/**
 * Plain-language description of the plotted data, used as the chart's accessible description. It reports what the
 * model predicted (never "actual" remaining life) and states anything unusual: several versions, shared cycles.
 */
export function describeChart(geometry: ChartGeometry): string {
  const { points, versions, duplicateCycles, skipped } = geometry
  const first = points[0] as PlottedPoint
  const last = points[points.length - 1] as PlottedPoint
  const lowest = points.reduce((best, p) => (p.rul < best.rul ? p : best), first)
  const count = `${points.length} ${pluralize(points.length, 'prediction')}`
  const parts: string[] = []
  parts.push(
    first.cycle === last.cycle
      ? `Predicted remaining useful life, ${count} at cycle ${first.cycle}.`
      : `Predicted remaining useful life, ${count} from cycle ${first.cycle} to cycle ${last.cycle}.`,
  )
  parts.push(`Latest point: cycle ${last.cycle}, predicted RUL ${formatRul(last.rul)} cycles.`)
  if (points.length > 1) parts.push(`Lowest predicted RUL: ${formatRul(lowest.rul)} cycles at cycle ${lowest.cycle}.`)
  if (versions.length > 1) parts.push(`Predictions come from ${versions.length} model versions: ${versions.join(', ')}.`)
  else parts.push(`Model version: ${versions[0]}.`)
  if (duplicateCycles > 0) {
    parts.push(`${duplicateCycles} ${pluralize(duplicateCycles, 'cycle has', 'cycles have')} more than one prediction; all are plotted.`)
  }
  if (skipped > 0) parts.push(`${skipped} ${pluralize(skipped, 'prediction')} could not be plotted.`)
  return parts.join(' ')
}
