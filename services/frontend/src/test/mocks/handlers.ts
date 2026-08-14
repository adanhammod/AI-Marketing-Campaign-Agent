import { http, HttpResponse } from 'msw'

const now = '2026-08-05T00:00:00Z'

export const campaignSummaryFixture = {
  campaign_id: '11111111-1111-4111-8111-111111111111',
  title: 'Summer Launch',
  current_version: 1,
  latest_final_version: null,
  status: 'CREATED',
  current_step: null,
  progress_percent: 0,
  created_at: now,
  updated_at: now,
}

export const campaignDetailFixture = {
  campaign_id: '11111111-1111-4111-8111-111111111111',
  campaign_version: 1,
  title: 'Summer Launch',
  current_version: 1,
  latest_final_version: null,
  event_sequence: 0,
  actions: {},
  status: 'CREATED',
  current_step: null,
  progress_percent: 0,
  brief: {
    business_name: 'Acme Co',
    product_or_service: 'Widgets',
    business_description: 'We make high quality widgets for discerning customers.',
    campaign_goal: 'Drive summer sales',
    platforms: ['instagram'],
    tone: 'playful',
    language: 'en',
    brand_colors: [],
  },
  strategy: null,
  campaign_copy: null,
  storyboard: null,
  artifacts: [],
  revision: null,
  approval: null,
  completed_steps: [],
  retry_eligible: false,
  error: null,
  created_at: now,
  updated_at: now,
}

export const campaignCreationAcceptedFixture = {
  campaign_id: '11111111-1111-4111-8111-111111111111',
  campaign_version: 1,
  job_id: '22222222-2222-4222-8222-222222222222',
  status: 'CREATED',
  progress_percent: 0,
  links: {},
}

export const campaignArtifactsFixture = {
  campaign_id: '11111111-1111-4111-8111-111111111111',
  campaign_version: 1,
  items: [],
}

export const handlers = [
  http.get('/api/v1/campaigns', () =>
    HttpResponse.json({ items: [campaignSummaryFixture], next_cursor: null }),
  ),
  http.get('/api/v1/campaigns/:campaignId', () => HttpResponse.json(campaignDetailFixture)),
  http.get('/api/v1/campaigns/:campaignId/artifacts', () =>
    HttpResponse.json(campaignArtifactsFixture),
  ),
  http.post('/api/v1/campaigns', () =>
    HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 }),
  ),
]
