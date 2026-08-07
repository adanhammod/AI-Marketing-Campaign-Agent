import type { components } from '../api/schema.gen'

export type CampaignStatus = components['schemas']['CampaignStatus']

export type AssetStatus = 'pending' | 'generating' | 'ready' | 'failed'

// Metadata surfaced by the glass reveal panel once an asset reaches 'ready'.
// Only defined for asset kinds this pass covers (images/voiceover/copy/strategy).
export type AssetMetadata =
  | { kind: 'images'; resolution: string; aspectRatio: string }
  | { kind: 'voiceover'; durationSeconds: number; wordCount: number }
  | { kind: 'copy'; platformCharacterCounts: { platform: string; characters: number }[] }
  | { kind: 'strategy'; positioningPoints: number }

export interface AssetFixture {
  key: 'strategy' | 'copy' | 'storyboard' | 'images' | 'voiceover' | 'video'
  title: string
  description: string
  status: AssetStatus
  metadata?: AssetMetadata
}

// Demo campaign title — stands in for the real CampaignDetailResponse.title field
// (already modeled by the API) until this page wires up live data (see PRODUCT.md).
export const DEMO_CAMPAIGN_TITLE = 'Summer Product Launch'

// Demo pipeline position — matches DEMO_ASSETS below (storyboard is the one
// actively generating). Drives the PipelineStepper; not live-polled (see PRODUCT.md).
export const DEMO_STATUS: CampaignStatus = 'GENERATING_STORYBOARD'

// Demo snapshot of a campaign mid-pipeline — no live polling yet (see PRODUCT.md).
// Strategy/Copy are done, Storyboard is actively generating, Images/Voiceover/Video
// haven't started. This is fixture data, not a claim about any real campaign.
export const DEMO_ASSETS: AssetFixture[] = [
  {
    key: 'strategy',
    title: 'Marketing Strategy',
    description: 'Positioning, audience, and channel plan.',
    status: 'ready',
    metadata: { kind: 'strategy', positioningPoints: 4 },
  },
  {
    key: 'copy',
    title: 'Social Copy',
    description: 'On-brand captions for every platform.',
    status: 'ready',
    metadata: {
      kind: 'copy',
      platformCharacterCounts: [
        { platform: 'Instagram', characters: 148 },
        { platform: 'TikTok', characters: 120 },
        { platform: 'Facebook', characters: 211 },
        { platform: 'YouTube', characters: 96 },
        { platform: 'LinkedIn', characters: 264 },
      ],
    },
  },
  {
    key: 'storyboard',
    title: 'Storyboard',
    description: 'Shot-by-shot breakdown.',
    status: 'generating',
  },
  {
    key: 'images',
    title: 'Images',
    description: 'Visuals matched to your brand.',
    status: 'pending',
    // Resolution/aspect ratio read directly off the committed fixture SVGs
    // (viewBox 0 0 640 400) — not fabricated, just not yet 'ready'.
    metadata: { kind: 'images', resolution: '640 × 400 px', aspectRatio: '16:10' },
  },
  {
    key: 'voiceover',
    title: 'Voiceover',
    description: 'AI-narrated audio in your voice.',
    status: 'pending',
    metadata: { kind: 'voiceover', durationSeconds: 28, wordCount: 74 },
  },
  {
    key: 'video',
    title: 'Final Video',
    description: 'Assembled, polished, ready to publish.',
    status: 'pending',
  },
]

export const STATUS_LABEL: Record<AssetStatus, string> = {
  pending: 'Not started',
  generating: 'Generating…',
  ready: 'Ready',
  failed: "Couldn't generate",
}
