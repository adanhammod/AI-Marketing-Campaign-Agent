import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'

import { CreateCampaignPage } from './CreateCampaignPage'
import { Home } from './Home'

function renderHome() {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/campaigns/new" element={<CreateCampaignPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('Home', () => {
  it('renders the hero headline and the create-campaign CTA', async () => {
    renderHome()

    // AureleHero is lazy-loaded, and jsdom has no WebGL, so this resolves
    // to the same static fallback markup either way — findBy* waits out
    // both the lazy import and the WebGL-failure fallback path.
    expect(
      await screen.findByRole('heading', { name: 'One brief. Every asset. Perfectly in sync.' }),
    ).toBeInTheDocument()
    expect(
      await screen.findByRole('link', { name: /create your campaign/i }),
    ).toHaveAttribute('href', '/campaigns/new')
  })
})
