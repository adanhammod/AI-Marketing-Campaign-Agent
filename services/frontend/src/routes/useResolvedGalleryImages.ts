import { useRef } from 'react'

import type { components } from '../api/schema.gen'
import type { GalleryImage } from '../components/detail/ImageGallery'

type PublicArtifactReference = components['schemas']['PublicArtifactReference']

// Signed download URLs are re-issued on every poll and may differ even when
// they still point at the same object. Refreshing `src` on every poll makes
// the browser reload the image (visible flicker), so a resolved URL is kept
// stable across polls until it's actually close to expiring.
export const URL_EXPIRY_REFRESH_MARGIN_MS = 30_000

interface ResolvedUrl {
  url: string
  expiresAtMs: number | null // null = no expiry info -> treat as always valid
}

export function useResolvedGalleryImages(
  artifacts: PublicArtifactReference[] | undefined,
  now: () => number = Date.now,
): GalleryImage[] {
  const resolved = useRef<Map<string, ResolvedUrl>>(new Map())
  const seen = new Set<string>()
  const images: GalleryImage[] = []

  for (const artifact of artifacts ?? []) {
    if (artifact.artifact_type !== 'IMAGE') continue
    const artifactId = artifact.artifact_id
    if (seen.has(artifactId)) continue // duplicate artifact_id in one response -> one card
    seen.add(artifactId)

    const freshUrl = artifact.download_url ?? undefined
    const parsedExpiry = artifact.download_url_expires_at ? Date.parse(artifact.download_url_expires_at) : NaN
    const freshExpiresAtMs = Number.isNaN(parsedExpiry) ? null : parsedExpiry

    const existing = resolved.current.get(artifactId)
    const existingValid =
      existing != null &&
      (existing.expiresAtMs === null || existing.expiresAtMs - now() > URL_EXPIRY_REFRESH_MARGIN_MS)

    let chosen: ResolvedUrl | undefined
    if (existingValid) {
      chosen = existing // keep the working URL stable -- do not swap src for no reason
    } else if (freshUrl) {
      chosen = { url: freshUrl, expiresAtMs: freshExpiresAtMs }
      resolved.current.set(artifactId, chosen)
    } else {
      chosen = existing // backend gave nothing usable this poll -- don't make the image vanish
    }

    if (!chosen?.url) continue
    images.push({
      artifactId,
      url: chosen.url,
      sceneNumber: artifact.scene_number,
      attribution: artifact.attribution?.attribution_text,
    })
  }

  return images.sort((a, b) => (a.sceneNumber ?? 0) - (b.sceneNumber ?? 0))
}
