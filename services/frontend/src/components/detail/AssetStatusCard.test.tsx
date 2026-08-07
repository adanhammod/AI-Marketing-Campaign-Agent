import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AssetStatusCard } from './AssetStatusCard'
import type { AssetFixture } from '../../routes/CampaignDetailPage.fixtures'

function asset(overrides: Partial<AssetFixture>): AssetFixture {
  return {
    key: 'images',
    title: 'Images',
    description: 'Visuals matched to your brand.',
    status: 'pending',
    ...overrides,
  }
}

describe('AssetStatusCard', () => {
  it('keeps the placeholder canvas for pending/generating images and shows no reveal panel', () => {
    render(
      <AssetStatusCard
        asset={asset({
          status: 'pending',
          metadata: { kind: 'images', resolution: '640 × 400 px', aspectRatio: '16:10' },
        })}
      />,
    )

    expect(screen.queryAllByRole('img')).toHaveLength(0)
    expect(screen.queryByText(/640 × 400 px/)).not.toBeInTheDocument()
  })

  it('renders the real fixture images and a reveal panel once the images asset is ready', () => {
    render(
      <AssetStatusCard
        asset={asset({
          status: 'ready',
          metadata: { kind: 'images', resolution: '640 × 400 px', aspectRatio: '16:10' },
        })}
      />,
    )

    expect(screen.getAllByRole('img')).toHaveLength(3)
    expect(screen.getByText('640 × 400 px · 16:10')).toBeInTheDocument()
  })

  it('formats voiceover metadata as duration and word count once ready', () => {
    render(
      <AssetStatusCard
        asset={asset({
          key: 'voiceover',
          title: 'Voiceover',
          description: 'AI-narrated audio in your voice.',
          status: 'ready',
          metadata: { kind: 'voiceover', durationSeconds: 28, wordCount: 74 },
        })}
      />,
    )

    expect(screen.getByText('28s · 74 words')).toBeInTheDocument()
  })

  it('formats copy metadata as per-platform character counts once ready', () => {
    render(
      <AssetStatusCard
        asset={asset({
          key: 'copy',
          title: 'Social Copy',
          description: 'On-brand captions for every platform.',
          status: 'ready',
          metadata: {
            kind: 'copy',
            platformCharacterCounts: [
              { platform: 'Instagram', characters: 148 },
              { platform: 'TikTok', characters: 120 },
            ],
          },
        })}
      />,
    )

    expect(screen.getByText('Instagram 148 · TikTok 120')).toBeInTheDocument()
  })

  it('formats strategy metadata as a positioning-point count once ready', () => {
    render(
      <AssetStatusCard
        asset={asset({
          key: 'strategy',
          title: 'Marketing Strategy',
          description: 'Positioning, audience, and channel plan.',
          status: 'ready',
          metadata: { kind: 'strategy', positioningPoints: 4 },
        })}
      />,
    )

    expect(screen.getByText('4 positioning points')).toBeInTheDocument()
  })

  it('shows no reveal panel when ready but no metadata is present', () => {
    render(
      <AssetStatusCard
        asset={asset({
          key: 'storyboard',
          title: 'Storyboard',
          description: 'Shot-by-shot breakdown.',
          status: 'ready',
        })}
      />,
    )

    expect(screen.queryByText(/positioning|words|px|·/)).not.toBeInTheDocument()
  })
})
