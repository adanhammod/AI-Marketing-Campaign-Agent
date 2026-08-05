import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { ApiError } from './client'
import { createQueryClient } from './queryClient'

function retryFn(queryClient: QueryClient) {
  const retry = queryClient.getDefaultOptions().queries?.retry
  if (typeof retry !== 'function') throw new Error('expected a retry function')
  return retry as (failureCount: number, error: unknown) => boolean
}

describe('createQueryClient', () => {
  it('returns a QueryClient instance', () => {
    expect(createQueryClient()).toBeInstanceOf(QueryClient)
  })

  it('returns a fresh instance on each call so caches never leak between callers', () => {
    expect(createQueryClient()).not.toBe(createQueryClient())
  })

  it('does not retry queries that fail with a 4xx ApiError', () => {
    const retry = retryFn(createQueryClient())

    expect(retry(0, new ApiError(404, { detail: 'not found' }))).toBe(false)
  })

  it('retries queries that fail with a 5xx ApiError up to 3 times', () => {
    const retry = retryFn(createQueryClient())

    expect(retry(0, new ApiError(503, {}))).toBe(true)
    expect(retry(2, new ApiError(503, {}))).toBe(true)
    expect(retry(3, new ApiError(503, {}))).toBe(false)
  })
})
