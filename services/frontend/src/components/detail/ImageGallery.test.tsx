import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ImageGallery, type GalleryImage } from './ImageGallery'

const image = (overrides: Partial<GalleryImage> = {}): GalleryImage => ({
  artifactId: 'a1',
  url: 'https://cdn.example/scene-1.jpg?sig=1',
  sceneNumber: 1,
  attribution: null,
  ...overrides,
})

describe('ImageGallery', () => {
  it('keys cards by artifactId, not by url, so a re-signed URL does not remount the image', () => {
    const { container, rerender } = render(
      <ImageGallery images={[image({ url: 'https://cdn.example/scene-1.jpg?sig=1' })]} status="ready" />,
    )
    const firstImg = container.querySelector('img')
    expect(firstImg).not.toBeNull()

    rerender(<ImageGallery images={[image({ url: 'https://cdn.example/scene-1.jpg?sig=2' })]} status="ready" />)
    const secondImg = container.querySelector('img')

    // Same DOM node reused (no remount) -- proves the stable artifactId key.
    expect(secondImg).toBe(firstImg)
    // The node's src attribute still updates to whatever URL it's given.
    expect(secondImg).toHaveAttribute('src', 'https://cdn.example/scene-1.jpg?sig=2')
  })

  it('adds a new artifact immediately without disturbing the existing card', () => {
    const { container, rerender } = render(<ImageGallery images={[image({ artifactId: 'a1' })]} status="ready" />)
    const firstImg = container.querySelector('img')

    rerender(
      <ImageGallery
        images={[image({ artifactId: 'a1' }), image({ artifactId: 'a2', url: 'https://cdn.example/scene-2.jpg', sceneNumber: 2 })]}
        status="ready"
      />,
    )

    const imgs = container.querySelectorAll('img')
    expect(imgs).toHaveLength(2)
    expect(imgs[0]).toBe(firstImg)
  })

  it('renders each image with its own correct scene number regardless of order', () => {
    render(
      <ImageGallery
        images={[
          image({ artifactId: 'a3', sceneNumber: 3 }),
          image({ artifactId: 'a1', sceneNumber: 1 }),
          image({ artifactId: 'a2', sceneNumber: 2 }),
        ]}
        status="ready"
      />,
    )

    expect(screen.getByAltText('Scene 3 visual for the campaign')).toBeInTheDocument()
    expect(screen.getByAltText('Scene 1 visual for the campaign')).toBeInTheDocument()
    expect(screen.getByAltText('Scene 2 visual for the campaign')).toBeInTheDocument()
  })
})
