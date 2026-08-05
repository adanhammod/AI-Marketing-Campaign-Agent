import createClient from 'openapi-fetch'

import type { paths } from './schema.gen'

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown) {
    super(`API request failed with status ${status}`)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

function resolveBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL
  if (configured) return configured
  return typeof window !== 'undefined' ? window.location.origin : ''
}

export const apiClient = createClient<paths>({
  baseUrl: resolveBaseUrl(),
  fetch: (input) => globalThis.fetch(input),
})

type ApiResult<T> = Promise<{ data?: T; error?: unknown; response: Response }>

export async function unwrap<T>(result: ApiResult<T>): Promise<T> {
  const { data, error, response } = await result

  if (error !== undefined) {
    throw new ApiError(response.status, error)
  }

  if (data === undefined) {
    throw new ApiError(response.status, undefined)
  }

  return data
}
