import type { AlertSeverity, AlertStatus, Model, Origin, Risk } from '../api/schemas'
import { Badge } from './ui'
import type { Tone } from './ui'

const riskTone: Record<Risk, Tone> = { normal: 'ok', warning: 'warn', critical: 'bad' }
const riskLabel: Record<Risk, string> = { normal: 'Normal', warning: 'Warning', critical: 'Critical' }

/** Predicted risk category; `null` means the engine has no stored prediction yet. */
export function RiskBadge({ risk }: { risk: Risk | null }) {
  if (risk === null) return <Badge tone="neutral">No prediction</Badge>
  return <Badge tone={riskTone[risk]}>{riskLabel[risk]}</Badge>
}

const severityTone: Record<AlertSeverity, Tone> = { warning: 'warn', critical: 'bad' }

export function AlertSeverityBadge({ severity }: { severity: AlertSeverity }) {
  return <Badge tone={severityTone[severity]}>{severity === 'critical' ? 'Critical' : 'Warning'}</Badge>
}

const alertStatusTone: Record<AlertStatus, Tone> = { open: 'warn', acknowledged: 'neutral', resolved: 'ok' }
const alertStatusLabel: Record<AlertStatus, string> = { open: 'Open', acknowledged: 'Acknowledged', resolved: 'Resolved' }

export function AlertStatusBadge({ status }: { status: AlertStatus }) {
  return <Badge tone={alertStatusTone[status]}>{alertStatusLabel[status]}</Badge>
}

/**
 * Where an engine's data came from. `api` is ordinary ingestion (provenance is not claimed to be real field data);
 * `simulation` only if the engine was created by a validated simulation run.
 */
export function OriginBadge({ origin }: { origin: Origin }) {
  return <Badge tone="neutral">{origin === 'simulation' ? 'Simulated' : 'Ordinary ingestion'}</Badge>
}

const modelStatusTone: Record<Model['status'], Tone> = { active: 'ok', candidate: 'warn', archived: 'neutral' }
const modelStatusLabel: Record<Model['status'], string> = { active: 'Active', candidate: 'Candidate', archived: 'Archived' }

export function ModelStatusBadge({ status }: { status: Model['status'] }) {
  return <Badge tone={modelStatusTone[status]}>{modelStatusLabel[status]}</Badge>
}
