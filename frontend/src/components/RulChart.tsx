import { useId } from 'react'
import type { Prediction, Risk } from '../api/schemas'
import { buildChartGeometry, describeChart, formatTick, lineDash, markerShapeName } from '../lib/chart'
import type { MarkerShape, PlottedPoint } from '../lib/chart'
import { formatRul } from '../lib/format'

// Marker fill = the risk category the API reported with that prediction. Marker SHAPE and line pattern = model version.
// Neither is colour-only: risk is also in the table and the legend text, versions are named in the legend.
const riskFill: Record<Risk, string> = { normal: 'fill-emerald-600', warning: 'fill-amber-500', critical: 'fill-red-600' }
const riskLabel: Record<Risk, string> = { normal: 'Normal', warning: 'Warning', critical: 'Critical' }
const RISKS: readonly Risk[] = ['normal', 'warning', 'critical']

function markerPath(shape: MarkerShape, x: number, y: number): string | null {
  switch (shape) {
    case 'triangle':
      return `M ${x} ${y - 6} L ${x + 5.5} ${y + 4.5} L ${x - 5.5} ${y + 4.5} Z`
    case 'diamond':
      return `M ${x} ${y - 6.5} L ${x + 6.5} ${y} L ${x} ${y + 6.5} L ${x - 6.5} ${y} Z`
    case 'inverted triangle':
      return `M ${x} ${y + 6} L ${x + 5.5} ${y - 4.5} L ${x - 5.5} ${y - 4.5} Z`
    default:
      return null
  }
}

/** One marker. `className` supplies fill and stroke; the optional title is the hover text for that point. */
function Marker({ shape, x, y, className, title }: { shape: MarkerShape; x: number; y: number; className: string; title?: string }) {
  const label = title ? <title>{title}</title> : null
  if (shape === 'circle') {
    return (
      <circle cx={x} cy={y} r={5} className={className}>
        {label}
      </circle>
    )
  }
  if (shape === 'square') {
    return (
      <rect x={x - 4.5} y={y - 4.5} width={9} height={9} className={className}>
        {label}
      </rect>
    )
  }
  return (
    <path d={markerPath(shape, x, y) ?? ''} className={className}>
      {label}
    </path>
  )
}

function pointTitle(p: PlottedPoint): string {
  return `Cycle ${p.cycle}: predicted RUL ${formatRul(p.rul)} cycles, ${riskLabel[p.risk]} risk, model ${p.version}`
}

/**
 * Predicted RUL against cycle as an inline SVG: no chart library. The geometry is computed by `buildChartGeometry`;
 * this component only draws it. Every prediction is plotted, including several at one cycle from different model versions.
 *
 * Accessibility: the SVG is `role="img"` with a title and a plain-language description; the full data is in the
 * prediction table next to it. Responsive: the SVG scales with a viewBox but never below a readable width: narrower
 * screens scroll the chart sideways (a focusable region) instead of shrinking the text.
 */
export function RulChart({ predictions }: { predictions: readonly Pick<Prediction, 'id' | 'cycle' | 'predicted_rul' | 'risk_category' | 'model_version'>[] }) {
  const id = useId()
  const geometry = buildChartGeometry(predictions)
  if (geometry === null) {
    return <p className="text-slate-600">There are no predictions to plot.</p>
  }

  const { width, height, plot, points, series, versions, xTicks, yTicks } = geometry
  const titleId = `${id}-title`
  const descId = `${id}-desc`
  const description = describeChart(geometry)

  return (
    <div>
      <div
        role="region"
        aria-label="Predicted RUL chart (scrolls sideways on narrow screens)"
        tabIndex={0}
        className="overflow-x-auto rounded-md border border-slate-200 bg-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      >
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-labelledby={`${titleId} ${descId}`}
          className="block h-auto w-full min-w-[34rem]"
        >
          <title id={titleId}>Predicted RUL by cycle</title>
          <desc id={descId}>{description}</desc>

          {/* grid and axes */}
          {yTicks.map((tick) => (
            <g key={`y-${tick.value}`}>
              <line x1={plot.left} x2={plot.right} y1={tick.position} y2={tick.position} className="stroke-slate-200" strokeWidth={1} />
              <text x={plot.left - 8} y={tick.position + 4} textAnchor="end" fontSize={12} className="fill-slate-600">
                {formatTick(tick.value)}
              </text>
            </g>
          ))}
          {xTicks.map((tick) => (
            <g key={`x-${tick.value}`}>
              <line x1={tick.position} x2={tick.position} y1={plot.bottom} y2={plot.bottom + 5} className="stroke-slate-400" strokeWidth={1} />
              <text x={tick.position} y={plot.bottom + 20} textAnchor="middle" fontSize={12} className="fill-slate-600">
                {formatTick(tick.value)}
              </text>
            </g>
          ))}
          <line x1={plot.left} x2={plot.left} y1={plot.top} y2={plot.bottom} className="stroke-slate-500" strokeWidth={1} />
          <line x1={plot.left} x2={plot.right} y1={plot.bottom} y2={plot.bottom} className="stroke-slate-500" strokeWidth={1} />
          <text x={(plot.left + plot.right) / 2} y={height - 8} textAnchor="middle" fontSize={13} className="fill-slate-700">
            Cycle (operating cycle number)
          </text>
          <text
            x={16}
            y={(plot.top + plot.bottom) / 2}
            textAnchor="middle"
            fontSize={13}
            transform={`rotate(-90 16 ${(plot.top + plot.bottom) / 2})`}
            className="fill-slate-700"
          >
            Predicted RUL (cycles)
          </text>

          {/* one line per model version, then every point on top */}
          {series.map((s) =>
            s.pointCount > 1 ? (
              <path
                key={`line-${s.version}`}
                d={s.path}
                fill="none"
                strokeWidth={1.5}
                strokeDasharray={lineDash(s.shape) || undefined}
                className="stroke-slate-500"
              />
            ) : null,
          )}
          {points.map((p) => (
            <Marker
              key={p.id}
              shape={markerShapeName(p.shape)}
              x={p.x}
              y={p.y}
              className={`${riskFill[p.risk]} stroke-slate-900`}
              title={pointTitle(p)}
            />
          ))}
        </svg>
      </div>

      <div className="mt-3 grid gap-4 text-sm sm:grid-cols-2">
        <div>
          <h3 className="font-semibold">Model versions</h3>
          <ul className="mt-1 space-y-1">
            {geometry.versions.map((version, shape) => (
              <li key={version} className="flex items-center gap-2">
                <svg viewBox="0 0 28 14" width={28} height={14} aria-hidden="true">
                  <line x1={0} x2={28} y1={7} y2={7} strokeWidth={1.5} strokeDasharray={lineDash(shape) || undefined} className="stroke-slate-500" />
                  <Marker shape={markerShapeName(shape)} x={14} y={7} className="fill-slate-300 stroke-slate-900" />
                </svg>
                <span className="break-all">{version}</span>
                <span className="text-slate-600">
                  ({markerShapeName(shape)} markers{lineDash(shape) ? ', dashed line' : ', solid line'})
                </span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="font-semibold">Risk category (marker colour)</h3>
          <ul className="mt-1 space-y-1">
            {RISKS.map((risk) => (
              <li key={risk} className="flex items-center gap-2">
                <svg viewBox="0 0 14 14" width={14} height={14} aria-hidden="true">
                  <circle cx={7} cy={7} r={5} className={`${riskFill[risk]} stroke-slate-900`} />
                </svg>
                {riskLabel[risk]}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-3 text-sm text-slate-600">
        Predicted RUL is the model&apos;s estimate, not the engine&apos;s actual remaining life. Colours are the risk category
        the API reported with each prediction; no thresholds are drawn.
      </p>
      {geometry.duplicateCycles > 0 && (
        <p className="mt-1 text-sm text-slate-600">
          {geometry.duplicateCycles} {geometry.duplicateCycles === 1 ? 'cycle has' : 'cycles have'} more than one prediction (from
          different model versions). Every prediction is plotted.
        </p>
      )}
      {versions.length > 1 && (
        <p className="mt-1 text-sm text-slate-600">
          These predictions come from {versions.length} model versions; compare them by marker shape and line pattern.
        </p>
      )}
    </div>
  )
}
