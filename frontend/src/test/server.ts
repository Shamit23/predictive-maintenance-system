import { setupServer } from 'msw/node'

/** Starts with no handlers: every test declares the responses it needs. */
export const server = setupServer()
