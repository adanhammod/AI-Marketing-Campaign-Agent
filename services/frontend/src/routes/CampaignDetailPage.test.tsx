import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { CampaignDetailPage } from './CampaignDetailPage'
import { DEMO_ASSETS, STATUS_LABEL } from './CampaignDetailPage.fixtures'

function renderDetailPage(campaignId = '11111111-1111-4111-8111-111111111111') {
  render(
    <MemoryRouter initialEntries={[`/campaigns/${campaignId}`]}>
      <Routes>
        <Route path="/campaigns/:campaignId" element={<CampaignDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('CampaignDetailPage', () => {
  it('renders the campaign id taken from the route', () => {
    renderDetailPage()

    expect(
      screen.getByRole('heading', { name: /11111111-1111-4111-8111-111111111111/ }),
    ).toBeInTheDocument()
  })

  it('renders every demo asset with its title and status label', () => {
    renderDetailPage()

    const list = screen.getByRole('list', { name: 'Campaign assets' })
    for (const asset of DEMO_ASSETS) {
      const item = within(list)
        .getByRole('heading', { name: asset.title })
        .closest('[role="listitem"]')
      expect(item).not.toBeNull()
      expect(within(item as HTMLElement).getByText(STATUS_LABEL[asset.status])).toBeInTheDocument()
    }
  })

  it("marks ready assets with data-status='ready' and pending assets with data-status='pending'", () => {
    renderDetailPage()

    const readyAsset = DEMO_ASSETS.find((asset) => asset.status === 'ready')
    const pendingAsset = DEMO_ASSETS.find((asset) => asset.status === 'pending')
    expect(readyAsset).toBeDefined()
    expect(pendingAsset).toBeDefined()

    const readyItem = screen
      .getByRole('heading', { name: readyAsset!.title })
      .closest('[role="listitem"]')
    const pendingItem = screen
      .getByRole('heading', { name: pendingAsset!.title })
      .closest('[role="listitem"]')

    expect(readyItem).toHaveAttribute('data-status', 'ready')
    expect(pendingItem).toHaveAttribute('data-status', 'pending')
  })
})
