import { renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { components } from '../api/schema.gen'
import { URL_EXPIRY_REFRESH_MARGIN_MS, useResolvedGalleryImages } from './useResolvedGalleryImages'

type PublicArtifactReference = components['schemas']['PublicArtifactReference']

const CAMPAIGN_ID = '11111111-1111-4111-8111-111111111111'
const NOW_MS = Date.parse('2026-08-23T00:00:00Z')

const imageArtifact = (overrides: Partial<PublicArtifactReference> = {}): PublicArtifactReference => ({
  artifact_id: 'a1',
  artifact_type: 'IMAGE',
  workflow_step: 'images',
  campaign_id: CAMPAIGN_ID,
  campaign_version: 1,
  mime_type: 'image/jpeg',
  size_bytes: 1,
  checksum_sha256: 'x',
  created_at: '2026-08-23T00:00:00Z',
  scene_number: 1,
  ...overrides,
})

const now = () => NOW_MS

describe('useResolvedGalleryImages', () => {
  it('returns the same url across polls when the url has not changed', () => {
    const artifact = imageArtifact({ download_url: 'https://cdn.example/a1.jpg?sig=1' })
    const { result, rerender } = renderHook(({ artifacts }) => useResolvedGalleryImages(artifacts, now), {
      initialProps: { artifacts: [artifact] },
    })
    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=1')

    rerender({ artifacts: [artifact] })
    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=1')
  })

  it('keeps the preserved url when polling returns a new signed url but the old one is still valid', () => {
    const farExpiry = new Date(NOW_MS + 15 * 60_000).toISOString() // 15 minutes out
    const { result, rerender } = renderHook(({ artifacts }) => useResolvedGalleryImages(artifacts, now), {
      initialProps: {
        artifacts: [imageArtifact({ download_url: 'https://cdn.example/a1.jpg?sig=1', download_url_expires_at: farExpiry })],
      },
    })
    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=1')

    rerender({
      artifacts: [imageArtifact({ download_url: 'https://cdn.example/a1.jpg?sig=2', download_url_expires_at: farExpiry })],
    })

    // Still valid -> the newly-signed url from this poll must NOT replace it.
    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=1')
  })

  it('replaces the preserved url once it has actually expired', () => {
    const pastExpiry = new Date(NOW_MS - 1_000).toISOString() // already expired
    const { result, rerender } = renderHook(({ artifacts }) => useResolvedGalleryImages(artifacts, now), {
      initialProps: {
        artifacts: [imageArtifact({ download_url: 'https://cdn.example/a1.jpg?sig=1', download_url_expires_at: pastExpiry })],
      },
    })
    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=1')

    rerender({
      artifacts: [imageArtifact({ download_url: 'https://cdn.example/a1.jpg?sig=2', download_url_expires_at: pastExpiry })],
    })

    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=2')
  })

  it('replaces the preserved url when it is within the refresh margin, even though not technically expired yet', () => {
    const nearExpiry = new Date(NOW_MS + URL_EXPIRY_REFRESH_MARGIN_MS - 1_000).toISOString() // inside the margin
    const { result, rerender } = renderHook(({ artifacts }) => useResolvedGalleryImages(artifacts, now), {
      initialProps: {
        artifacts: [imageArtifact({ download_url: 'https://cdn.example/a1.jpg?sig=1', download_url_expires_at: nearExpiry })],
      },
    })
    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=1')

    rerender({
      artifacts: [imageArtifact({ download_url: 'https://cdn.example/a1.jpg?sig=2', download_url_expires_at: nearExpiry })],
    })

    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=2')
  })

  it('does not make an already-visible image disappear when a poll returns no download_url', () => {
    const { result, rerender } = renderHook(({ artifacts }) => useResolvedGalleryImages(artifacts, now), {
      initialProps: { artifacts: [imageArtifact({ download_url: 'https://cdn.example/a1.jpg?sig=1' })] },
    })
    expect(result.current).toHaveLength(1)

    rerender({ artifacts: [imageArtifact({ download_url: null })] })

    expect(result.current).toHaveLength(1)
    expect(result.current[0].url).toBe('https://cdn.example/a1.jpg?sig=1')
  })

  it('picks up a brand-new artifact_id immediately without disturbing an existing preserved url', () => {
    const farExpiry = new Date(NOW_MS + 15 * 60_000).toISOString()
    const { result, rerender } = renderHook(({ artifacts }) => useResolvedGalleryImages(artifacts, now), {
      initialProps: {
        artifacts: [
          imageArtifact({ artifact_id: 'a1', scene_number: 1, download_url: 'https://cdn.example/a1.jpg?sig=1', download_url_expires_at: farExpiry }),
        ],
      },
    })
    expect(result.current).toHaveLength(1)

    rerender({
      artifacts: [
        imageArtifact({ artifact_id: 'a1', scene_number: 1, download_url: 'https://cdn.example/a1.jpg?sig=2', download_url_expires_at: farExpiry }),
        imageArtifact({ artifact_id: 'a2', scene_number: 2, download_url: 'https://cdn.example/a2.jpg?sig=1', download_url_expires_at: farExpiry }),
      ],
    })

    expect(result.current).toHaveLength(2)
    const byId = Object.fromEntries(result.current.map((image) => [image.artifactId, image]))
    expect(byId.a1.url).toBe('https://cdn.example/a1.jpg?sig=1') // preserved, unaffected by the new artifact
    expect(byId.a2.url).toBe('https://cdn.example/a2.jpg?sig=1')
  })

  it('collapses duplicate artifact_ids in a single response to one gallery item', () => {
    const { result } = renderHook(({ artifacts }) => useResolvedGalleryImages(artifacts, now), {
      initialProps: {
        artifacts: [
          imageArtifact({ artifact_id: 'a1', download_url: 'https://cdn.example/a1.jpg?sig=1' }),
          imageArtifact({ artifact_id: 'a1', download_url: 'https://cdn.example/a1.jpg?sig=1' }),
        ],
      },
    })

    expect(result.current).toHaveLength(1)
  })

  it('sorts and preserves the correct scene_number for multiple artifacts', () => {
    const { result } = renderHook(({ artifacts }) => useResolvedGalleryImages(artifacts, now), {
      initialProps: {
        artifacts: [
          imageArtifact({ artifact_id: 'a3', scene_number: 3, download_url: 'https://cdn.example/a3.jpg' }),
          imageArtifact({ artifact_id: 'a1', scene_number: 1, download_url: 'https://cdn.example/a1.jpg' }),
          imageArtifact({ artifact_id: 'a2', scene_number: 2, download_url: 'https://cdn.example/a2.jpg' }),
        ],
      },
    })

    expect(result.current.map((image) => image.sceneNumber)).toEqual([1, 2, 3])
    expect(result.current.map((image) => image.artifactId)).toEqual(['a1', 'a2', 'a3'])
  })
})
