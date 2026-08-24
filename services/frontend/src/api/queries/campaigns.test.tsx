import { QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { createQueryClient } from '../queryClient'
import { server } from '../../test/mocks/server'
import {
  campaignCreationAcceptedFixture,
  campaignDetailFixture,
  campaignSummaryFixture,
} from '../../test/mocks/handlers'
import {
  createIdempotencyKey,
  useCampaignDetail,
  useCampaignList,
  useCreateCampaign,
} from './campaigns'

afterEach(() => {
  vi.unstubAllGlobals()
})

function wrapperFor(queryClient = createQueryClient()) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useCampaignList', () => {
  it('sends offset/limit as query params and returns the typed list', async () => {
    let requestUrl: URL | undefined
    server.use(
      http.get('/api/v1/campaigns', ({ request }) => {
        requestUrl = new URL(request.url)
        return HttpResponse.json({ items: [campaignSummaryFixture], next_cursor: null })
      }),
    )

    const { result } = renderHook(() => useCampaignList({ offset: 20, limit: 10 }), {
      wrapper: wrapperFor(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data?.items).toEqual([campaignSummaryFixture])
    expect(requestUrl?.searchParams.get('offset')).toBe('20')
    expect(requestUrl?.searchParams.get('limit')).toBe('10')
  })
})

describe('useCampaignDetail', () => {
  it('returns the typed campaign detail for a given id', async () => {
    const { result } = renderHook(() => useCampaignDetail(campaignDetailFixture.campaign_id), {
      wrapper: wrapperFor(),
    })

    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    expect(result.current.data).toEqual(campaignDetailFixture)
  })

  it('surfaces a 404 as an error state', async () => {
    server.use(
      http.get('/api/v1/campaigns/:campaignId', () =>
        HttpResponse.json({ detail: 'not found' }, { status: 404 }),
      ),
    )

    const { result } = renderHook(() => useCampaignDetail(campaignDetailFixture.campaign_id), {
      wrapper: wrapperFor(),
    })

    await waitFor(() => expect(result.current.isError).toBe(true))
  })
})

describe('useCreateCampaign', () => {
  it('uses crypto.randomUUID for the Idempotency-Key when available', async () => {
    const randomUUID = vi.fn(() => '123e4567-e89b-42d3-a456-426614174000')
    vi.stubGlobal('crypto', { randomUUID, getRandomValues: vi.fn() })
    let idempotencyKey: string | null = null
    server.use(
      http.post('/api/v1/campaigns', ({ request }) => {
        idempotencyKey = request.headers.get('Idempotency-Key')
        return HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 })
      }),
    )

    const { result } = renderHook(() => useCreateCampaign(), { wrapper: wrapperFor() })

    const response = await result.current.mutateAsync({
      business_name: 'Acme Co',
      product_or_service: 'Widgets',
      business_description: 'We make high quality widgets for discerning customers.',
      campaign_goal: 'Drive summer sales',
      platforms: ['instagram'],
      tone: 'playful',
      language: 'en',
      brand_colors: [],
      video_style: 'VOICEOVER_AD',
    })

    expect(response).toEqual(campaignCreationAcceptedFixture)
    expect(idempotencyKey).toBe('123e4567-e89b-42d3-a456-426614174000')
    expect(randomUUID).toHaveBeenCalledOnce()
  })

  it('generates a UUID idempotency key when crypto.randomUUID is unavailable', () => {
    const getRandomValues = vi.fn((bytes: Uint8Array) => {
      bytes.set(Array.from({ length: 16 }, (_, index) => index))
      return bytes
    })
    vi.stubGlobal('crypto', { getRandomValues })

    expect(createIdempotencyKey()).toBe('00010203-0405-4607-8809-0a0b0c0d0e0f')
    expect(getRandomValues).toHaveBeenCalledOnce()
  })

  it('invalidates the campaign list cache on success', async () => {
    server.use(
      http.post('/api/v1/campaigns', () =>
        HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 }),
      ),
    )
    const queryClient = createQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    const { result } = renderHook(() => useCreateCampaign(), { wrapper: wrapperFor(queryClient) })

    await result.current.mutateAsync({
      business_name: 'Acme Co',
      product_or_service: 'Widgets',
      business_description: 'We make high quality widgets for discerning customers.',
      campaign_goal: 'Drive summer sales',
      platforms: ['instagram'],
      tone: 'playful',
      language: 'en',
      brand_colors: [],
      video_style: 'VOICEOVER_AD',
    })

    expect(invalidateSpy).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ['campaigns'] }))
  })
})
