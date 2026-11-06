/** The backend answered with an HTTP error. `code`/`message`/`details` come from its `{error:{...}}` envelope. */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

/** No HTTP response at all: backend down, proxy target unreachable, DNS, offline. */
export class NetworkError extends Error {
  constructor(message = 'Could not reach the API', options?: ErrorOptions) {
    super(message, options)
    this.name = 'NetworkError'
  }
}

/** The backend answered, but the body does not match the contract the frontend expects. */
export class ResponseValidationError extends Error {
  readonly path: string
  readonly issues: string[]

  constructor(path: string, issues: string[]) {
    super(`Unexpected response shape from ${path}`)
    this.name = 'ResponseValidationError'
    this.path = path
    this.issues = issues
  }
}

export type ErrorKind = 'network' | 'backend' | 'invalid' | 'unknown'

export function errorKind(error: unknown): ErrorKind {
  if (error instanceof NetworkError) return 'network'
  if (error instanceof ApiError) return 'backend'
  if (error instanceof ResponseValidationError) return 'invalid'
  return 'unknown'
}

/** Human-readable one-liner for the UI. Never includes headers or request data. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} (HTTP ${error.status}, ${error.code})`
  if (error instanceof ResponseValidationError) return `${error.message}: ${error.issues.slice(0, 3).join('; ')}`
  if (error instanceof Error) return error.message
  return 'Unknown error'
}
