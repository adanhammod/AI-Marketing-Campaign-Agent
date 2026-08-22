import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import { createQueryClient } from '../../api/queryClient'
import { server } from '../../test/mocks/server'
import { campaignCreationAcceptedFixture } from '../../test/mocks/handlers'
import { CampaignForm } from './CampaignForm'

function renderForm(onCreated = vi.fn()) {
  const queryClient = createQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <CampaignForm onCreated={onCreated} />
    </QueryClientProvider>,
  )
  return { onCreated }
}

async function fillValidForm(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Business name'), 'Acme Co')
  await user.type(screen.getByLabelText('Product or service'), 'Widgets')
  await user.type(
    screen.getByLabelText('Business description'),
    'We make high quality widgets for discerning customers everywhere.',
  )
  await user.type(screen.getByLabelText('Campaign goal'), 'Drive summer sales')
  await user.click(screen.getByLabelText('Instagram'))
  await user.type(screen.getByLabelText('Tone'), 'playful')
  await user.type(screen.getByLabelText('Language'), 'en')
}

describe('CampaignForm', () => {
  it('renders every field with an accessible label', () => {
    renderForm()

    expect(screen.getByLabelText('Business name')).toBeInTheDocument()
    expect(screen.getByLabelText('Product or service')).toBeInTheDocument()
    expect(screen.getByLabelText('Business description')).toBeInTheDocument()
    expect(screen.getByLabelText('Campaign goal')).toBeInTheDocument()
    expect(screen.getByLabelText('Tone')).toBeInTheDocument()
    expect(screen.getByLabelText('Language')).toBeInTheDocument()
    expect(screen.getByLabelText('Target audience (optional)')).toBeInTheDocument()
    expect(screen.getByLabelText('Key message (optional)')).toBeInTheDocument()
    expect(screen.getByLabelText('Call to action (optional)')).toBeInTheDocument()
    expect(screen.getByLabelText('Brand colors (optional)')).toBeInTheDocument()

    const platformGroup = screen.getByRole('group', { name: 'Platforms' })
    expect(within(platformGroup).getByLabelText('Instagram')).toBeInTheDocument()
    expect(within(platformGroup).getByLabelText('TikTok')).toBeInTheDocument()
    expect(within(platformGroup).getByLabelText('Facebook')).toBeInTheDocument()
    expect(within(platformGroup).getByLabelText('YouTube')).toBeInTheDocument()
    expect(within(platformGroup).getByLabelText('LinkedIn')).toBeInTheDocument()

    expect(screen.getByRole('button', { name: 'Create campaign' })).toBeInTheDocument()
  })

  it('never calls the API merely from mounting', async () => {
    let requestCount = 0
    server.use(
      http.post('/api/v1/campaigns', () => {
        requestCount += 1
        return HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 })
      }),
    )

    renderForm()
    await new Promise((resolve) => setTimeout(resolve, 20))

    expect(requestCount).toBe(0)
  })

  it('shows validation errors on empty submit and does not call the API', async () => {
    let requestCount = 0
    server.use(
      http.post('/api/v1/campaigns', () => {
        requestCount += 1
        return HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    renderForm()

    await user.click(screen.getByRole('button', { name: 'Create campaign' }))

    expect(await screen.findByText('Business name is required.')).toBeInTheDocument()
    expect(screen.getByLabelText('Business name')).toHaveAttribute('aria-invalid', 'true')
    expect(requestCount).toBe(0)
  })

  it('moves focus to the first invalid field after a failed validation attempt', async () => {
    const user = userEvent.setup()
    renderForm()

    await user.click(screen.getByRole('button', { name: 'Create campaign' }))

    await waitFor(() => expect(screen.getByLabelText('Business name')).toHaveFocus())
  })

  it('disables the submit button while the request is pending', async () => {
    server.use(
      http.post('/api/v1/campaigns', async () => {
        await new Promise((resolve) => setTimeout(resolve, 50))
        return HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    renderForm()
    await fillValidForm(user)

    await user.click(screen.getByRole('button', { name: 'Create campaign' }))

    expect(screen.getByRole('button', { name: 'Create campaign' })).toBeDisabled()
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Create campaign' })).not.toBeDisabled(),
    )
  })

  it('submits the correctly shaped body with an Idempotency-Key header and reports the new campaign id', async () => {
    let capturedBody: unknown
    let idempotencyKey: string | null = null
    server.use(
      http.post('/api/v1/campaigns', async ({ request }) => {
        capturedBody = await request.json()
        idempotencyKey = request.headers.get('Idempotency-Key')
        return HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    const { onCreated } = renderForm()
    await fillValidForm(user)

    await user.click(screen.getByRole('button', { name: 'Create campaign' }))

    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith(campaignCreationAcceptedFixture.campaign_id),
    )
    expect(capturedBody).toMatchObject({
      business_name: 'Acme Co',
      product_or_service: 'Widgets',
      campaign_goal: 'Drive summer sales',
      platforms: ['instagram'],
      tone: 'playful',
      language: 'en',
    })
    expect(idempotencyKey).toBeTruthy()
  })

  it('submits successfully even when crypto.randomUUID is unavailable (insecure-context browsers)', async () => {
    // crypto.randomUUID() is restricted to secure contexts (HTTPS/localhost)
    // and is simply undefined on a plain-HTTP origin in real browsers --
    // this reproduces that by stubbing a crypto without it, keeping only
    // getRandomValues (which has no such restriction).
    const realCrypto = globalThis.crypto
    vi.stubGlobal('crypto', { getRandomValues: realCrypto.getRandomValues.bind(realCrypto) })

    let requestCount = 0
    server.use(
      http.post('/api/v1/campaigns', () => {
        requestCount += 1
        return HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 })
      }),
    )
    const user = userEvent.setup()
    const { onCreated } = renderForm()
    await fillValidForm(user)

    try {
      await user.click(screen.getByRole('button', { name: 'Create campaign' }))

      await waitFor(() =>
        expect(onCreated).toHaveBeenCalledWith(campaignCreationAcceptedFixture.campaign_id),
      )
      expect(requestCount).toBe(1)
      expect(
        screen.queryByText('Something went wrong while creating the campaign. Please try again.'),
      ).not.toBeInTheDocument()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('shows a normalized error banner and preserves entered values on a 409 response', async () => {
    server.use(
      http.post('/api/v1/campaigns', () =>
        HttpResponse.json(
          {
            error: {
              attempt: 1,
              code: 'IDEMPOTENCY_CONFLICT',
              component: 'FASTAPI',
              correlation_id: '33333333-3333-4333-8333-333333333333',
              message:
                'A campaign with this idempotency key already exists with a different brief.',
              retryable: false,
              schema_version: 1,
              timestamp: '2026-08-05T00:00:00Z',
            },
          },
          { status: 409 },
        ),
      ),
    )
    const user = userEvent.setup()
    renderForm()
    await fillValidForm(user)

    await user.click(screen.getByRole('button', { name: 'Create campaign' }))

    expect(
      await screen.findByText(
        'A campaign with this idempotency key already exists with a different brief.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Business name')).toHaveValue('Acme Co')
  })
})
