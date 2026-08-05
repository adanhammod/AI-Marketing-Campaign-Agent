import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { server } from '../test/mocks/server'
import { apiClient, ApiError, unwrap } from './client'

describe('unwrap', () => {
  it('resolves with the typed data on a successful response', async () => {
    server.use(http.get('/health/live', () => HttpResponse.json({ status: 'ok' })))

    const data = await unwrap(apiClient.GET('/health/live'))

    expect(data).toEqual({ status: 'ok' })
  })

  it('throws an ApiError with the status and parsed body on a non-2xx response', async () => {
    server.use(
      http.get('/health/live', () => HttpResponse.json({ detail: 'unavailable' }, { status: 503 })),
    )

    await expect(unwrap(apiClient.GET('/health/live'))).rejects.toBeInstanceOf(ApiError)
    await expect(unwrap(apiClient.GET('/health/live'))).rejects.toMatchObject({
      status: 503,
      body: { detail: 'unavailable' },
    })
  })
})
