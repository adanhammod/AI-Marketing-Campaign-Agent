import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { CampaignDetailPage } from './CampaignDetailPage'

describe('CampaignDetailPage', () => {
  it('renders the campaign id taken from the route', () => {
    render(
      <MemoryRouter initialEntries={['/campaigns/11111111-1111-4111-8111-111111111111']}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(
      screen.getByRole('heading', { name: /11111111-1111-4111-8111-111111111111/ }),
    ).toBeInTheDocument()
  })
})
