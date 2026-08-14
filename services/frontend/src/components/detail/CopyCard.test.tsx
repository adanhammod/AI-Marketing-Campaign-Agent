import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { CopyCard } from './CopyCard'

const copy = {
  headline: 'h',
  caption: 'c',
  call_to_action: 'cta',
  hashtags: [],
  channel_variants: [
    {
      channel: 'instagram',
      headline: 'IG headline',
      caption: 'IG caption',
      call_to_action: 'IG cta',
      hashtags: ['#a'],
    },
    {
      channel: 'tiktok',
      headline: 'TT headline',
      caption: 'TT caption',
      call_to_action: 'TT cta',
      hashtags: [],
    },
  ],
}

describe('CopyCard', () => {
  it('shows the first channel by default', () => {
    render(<CopyCard copy={copy} status="ready" />)
    expect(screen.getByText('IG caption')).toBeInTheDocument()
    expect(screen.queryByText('TT caption')).not.toBeInTheDocument()
  })

  it('switches panels when a different channel tab is clicked', async () => {
    const user = userEvent.setup()
    render(<CopyCard copy={copy} status="ready" />)
    await user.click(screen.getByRole('tab', { name: 'tiktok' }))
    expect(screen.getByText('TT caption')).toBeInTheDocument()
    expect(screen.queryByText('IG caption')).not.toBeInTheDocument()
  })

  it('marks the active tab with aria-selected', async () => {
    const user = userEvent.setup()
    render(<CopyCard copy={copy} status="ready" />)
    expect(screen.getByRole('tab', { name: 'instagram' })).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('tab', { name: 'tiktok' }))
    expect(screen.getByRole('tab', { name: 'tiktok' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'instagram' })).toHaveAttribute('aria-selected', 'false')
  })

  it('shows real per-channel character counts, not fabricated metadata', () => {
    render(<CopyCard copy={copy} status="ready" />)
    expect(screen.getByText('10 characters')).toBeInTheDocument()
  })

  it('shows a placeholder and no tabs when no copy has been generated yet', () => {
    render(<CopyCard copy={null} status="pending" />)
    expect(screen.getByText('Copy will appear here once generated.')).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })
})
