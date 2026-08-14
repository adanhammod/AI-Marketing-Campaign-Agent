import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { createQueryClient } from '../api/queryClient'
import { server } from '../test/mocks/server'
import { campaignCreationAcceptedFixture } from '../test/mocks/handlers'
import { CampaignDetailPage } from './CampaignDetailPage'
import { CreateCampaignPage } from './CreateCampaignPage'

function renderPage() {
  const queryClient = createQueryClient()
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/campaigns/new']}>
        <Routes>
          <Route path="/campaigns/new" element={<CreateCampaignPage />} />
          <Route path="/campaigns/:campaignId" element={<CampaignDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('CreateCampaignPage', () => {
  it('renders the campaign form', () => {
    renderPage()

    expect(screen.getByRole('heading', { name: 'Create campaign' })).toBeInTheDocument()
    expect(screen.getByLabelText('Business name')).toBeInTheDocument()
  })

  it('navigates to the new campaign detail route on successful submission', async () => {
    server.use(
      http.post('/api/v1/campaigns', () =>
        HttpResponse.json(campaignCreationAcceptedFixture, { status: 202 }),
      ),
    )
    const user = userEvent.setup()
    renderPage()

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

    await user.click(screen.getByRole('button', { name: 'Create campaign' }))

    expect(
      await screen.findByText(new RegExp(campaignCreationAcceptedFixture.campaign_id)),
    ).toBeInTheDocument()
  })
})
