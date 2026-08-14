import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { createQueryClient } from '../api/queryClient'
import { server } from '../test/mocks/server'
import { CampaignDetailPage } from './CampaignDetailPage'

const CAMPAIGN_ID = '11111111-1111-4111-8111-111111111111'
const now = '2026-08-14T00:00:00Z'

function baseDetail(overrides: Record<string, unknown> = {}) {
  return {
    campaign_id: CAMPAIGN_ID,
    campaign_version: 1,
    title: 'Luna Coffee: Luna Cold Brew',
    current_version: 1,
    latest_final_version: null,
    event_sequence: 1,
    actions: {},
    status: 'GENERATING_STORYBOARD',
    current_step: 'storyboard',
    progress_percent: 40,
    brief: {
      business_name: 'Luna Coffee',
      product_or_service: 'Luna Cold Brew',
      business_description: 'Small-batch cold brew.',
      campaign_goal: 'Increase online sales',
      platforms: ['instagram'],
      tone: 'warm',
      language: 'en-US',
      target_audience: 'Coffee lovers',
      call_to_action: 'Shop now',
      brand_colors: [],
    },
    strategy: null,
    copy: null,
    storyboard: null,
    artifacts: [],
    revision: null,
    approval: null,
    completed_steps: [],
    retry_eligible: false,
    error: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  }
}

function renderDetailPage(campaignId = CAMPAIGN_ID) {
  const queryClient = createQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/campaigns/${campaignId}`]}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function mockDetail(body: Record<string, unknown>) {
  server.use(http.get('/api/v1/campaigns/:campaignId', () => HttpResponse.json(body)))
}

function mockArtifacts(items: Record<string, unknown>[]) {
  server.use(
    http.get('/api/v1/campaigns/:campaignId/artifacts', () =>
      HttpResponse.json({ campaign_id: CAMPAIGN_ID, campaign_version: 1, items }),
    ),
  )
}

const imageArtifact = (overrides: Record<string, unknown> = {}) => ({
  artifact_id: 'a1',
  artifact_type: 'IMAGE',
  workflow_step: 'images',
  campaign_id: CAMPAIGN_ID,
  campaign_version: 1,
  mime_type: 'image/jpeg',
  size_bytes: 1,
  checksum_sha256: 'x',
  created_at: now,
  scene_number: 1,
  ...overrides,
})

const audioArtifact = (overrides: Record<string, unknown> = {}) => ({
  artifact_id: 'a2',
  artifact_type: 'AUDIO',
  workflow_step: 'voiceover',
  campaign_id: CAMPAIGN_ID,
  campaign_version: 1,
  mime_type: 'audio/mpeg',
  size_bytes: 1,
  checksum_sha256: 'x',
  created_at: now,
  provider: 'polly',
  ...overrides,
})

const videoArtifact = (overrides: Record<string, unknown> = {}) => ({
  artifact_id: 'a3',
  artifact_type: 'VIDEO',
  workflow_step: 'video',
  campaign_id: CAMPAIGN_ID,
  campaign_version: 1,
  mime_type: 'video/mp4',
  size_bytes: 1,
  checksum_sha256: 'x',
  created_at: now,
  ...overrides,
})

describe('CampaignDetailPage', () => {
  it('shows an honest loading state before the campaign loads, with no decorative spinner', () => {
    mockDetail(baseDetail())
    renderDetailPage()

    expect(screen.getByRole('heading', { name: 'Loading campaign…' })).toBeInTheDocument()
    expect(document.querySelector('svg[class*="spinner" i]')).toBeNull()
  })

  it('renders the real campaign title and status, not demo content', async () => {
    mockDetail(baseDetail({ title: 'Luna Coffee: Luna Cold Brew', status: 'READY_FOR_REVIEW' }))
    renderDetailPage()

    expect(
      await screen.findByRole('heading', { name: 'Luna Coffee: Luna Cold Brew' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Ready for review', { selector: 'span' })).toBeInTheDocument()
    expect(screen.queryByText('Summer Product Launch')).not.toBeInTheDocument()
    expect(screen.getByText(new RegExp(CAMPAIGN_ID))).toBeInTheDocument()
  })

  it('shows an error state when the campaign fails to load', async () => {
    server.use(
      http.get('/api/v1/campaigns/:campaignId', () =>
        HttpResponse.json({ detail: 'not found' }, { status: 404 }),
      ),
    )
    renderDetailPage()

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn.t load/i)
  })

  it('renders all workflow stages including Voiceover as its own distinct stage', async () => {
    mockDetail(baseDetail({ status: 'GENERATING_IMAGES', current_step: 'voiceover' }))
    renderDetailPage()

    const current = await screen.findByRole('listitem', { name: /Voiceover — current step/ })
    expect(current).toHaveAttribute('aria-current', 'step')
    expect(screen.getByRole('listitem', { name: /Images — complete/ })).toBeInTheDocument()
  })

  it('renders real strategy, copy, and storyboard content instead of fixture placeholders', async () => {
    mockDetail(
      baseDetail({
        status: 'GENERATING_IMAGES',
        current_step: 'images',
        strategy: {
          audience: 'Coffee lovers',
          positioning: 'Luna Coffee for Luna Cold Brew',
          objective: 'Increase sales',
          key_message: 'Premium coffee, every morning.',
          channel_rationale: { instagram: 'Reach coffee lovers' },
        },
        copy: {
          headline: 'Luna Coffee',
          caption: 'Premium coffee.',
          call_to_action: 'Shop now',
          hashtags: ['#Luna'],
          channel_variants: [
            {
              channel: 'instagram',
              headline: 'h',
              caption: 'twelve chars',
              call_to_action: 'Shop now',
              hashtags: [],
            },
          ],
        },
        storyboard: {
          scenes: [
            {
              scene_number: 1,
              purpose: 'Introduce the brand',
              duration_seconds: 5,
              narration: 'Meet Luna Cold Brew.',
              text_overlay: '',
              transition: 'cut',
              visual_prompt: 'Coffee cup on a table',
            },
          ],
          total_duration_seconds: 5,
        },
      }),
    )
    renderDetailPage()

    expect(await screen.findByText('Premium coffee, every morning.')).toBeInTheDocument()
    expect(screen.getByText('twelve chars')).toBeInTheDocument()
    expect(screen.getByText('Introduce the brand')).toBeInTheDocument()
    expect(screen.getByText('Meet Luna Cold Brew.')).toBeInTheDocument()
  })

  it('renders real signed artifact URLs and attribution for images, voiceover, and video once ready', async () => {
    mockDetail(
      baseDetail({
        status: 'READY_FOR_REVIEW',
        current_step: null,
        artifacts: [imageArtifact(), audioArtifact(), videoArtifact()],
      }),
    )
    mockArtifacts([
      imageArtifact({
        download_url: 'https://cdn.example/scene-1.jpg',
        attribution: {
          attribution_text: 'Photo by Jane Doe on Pexels',
          creator_name: 'Jane Doe',
          creator_profile_url: 'https://pexels.com/@jane',
          provider_asset_id: '123',
          provider_url: 'https://pexels.com',
          source_page_url: 'https://pexels.com/photo/123',
        },
      }),
      audioArtifact({ download_url: 'https://cdn.example/voiceover.mp3' }),
      videoArtifact({ download_url: 'https://cdn.example/final.mp4' }),
    ])
    renderDetailPage()

    expect(
      await screen.findByAltText('Scene 1 visual for the campaign'),
    ).toHaveAttribute('src', 'https://cdn.example/scene-1.jpg')
    expect(screen.getByText(/Photo by Jane Doe on Pexels/)).toBeInTheDocument()
    expect(document.querySelector('audio')).toHaveAttribute(
      'src',
      'https://cdn.example/voiceover.mp3',
    )
    expect(screen.getByText('polly')).toBeInTheDocument()
    expect(document.querySelector('video')).toHaveAttribute('src', 'https://cdn.example/final.mp4')
  })

  it('only shows the review actions card when the campaign is READY_FOR_REVIEW', async () => {
    mockDetail(baseDetail({ status: 'GENERATING_IMAGES', current_step: 'images' }))
    renderDetailPage()

    await screen.findByRole('heading', { name: 'Luna Coffee: Luna Cold Brew' })
    expect(screen.queryByRole('heading', { name: 'Ready for review' })).not.toBeInTheDocument()
  })

  it('shows the review actions card, with non-functional buttons, once READY_FOR_REVIEW', async () => {
    mockDetail(baseDetail({ status: 'READY_FOR_REVIEW', current_step: null }))
    renderDetailPage()

    expect(await screen.findByRole('heading', { name: 'Ready for review' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve campaign' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Request changes' })).toBeDisabled()
  })

  it("marks the specific failed step's card as failed using error.workflow_step", async () => {
    mockDetail(
      baseDetail({
        status: 'FAILED',
        current_step: null,
        error: {
          attempt: 1,
          code: 'PROVIDER_TIMEOUT',
          component: 'LANGGRAPH_WORKER',
          correlation_id: '33333333-3333-4333-8333-333333333333',
          message: 'timed out',
          retryable: true,
          schema_version: 1,
          timestamp: now,
          workflow_step: 'voiceover',
        },
      }),
    )
    renderDetailPage()

    const voiceoverCard = (await screen.findByRole('heading', { name: 'Voiceover' })).closest(
      'section',
    )
    expect(voiceoverCard).not.toBeNull()
    expect(voiceoverCard).toHaveTextContent("Couldn't generate")
  })
})
