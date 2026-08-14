import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PipelineStepper } from './PipelineStepper'

describe('PipelineStepper', () => {
  it('marks earlier stages complete and the matching stage current', () => {
    render(<PipelineStepper status="GENERATING_STORYBOARD" />)

    expect(screen.getByRole('listitem', { name: /Strategy — complete/ })).toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: /Copy — complete/ })).toBeInTheDocument()
    const current = screen.getByRole('listitem', { name: /Storyboard — current step/ })
    expect(current).toHaveAttribute('aria-current', 'step')
    expect(screen.getByRole('listitem', { name: /Images — upcoming/ })).toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: /Video — upcoming/ })).toBeInTheDocument()
    expect(screen.getByRole('listitem', { name: /Ready — upcoming/ })).toBeInTheDocument()
  })

  it('collapses CREATED and QUEUED onto the same queued stage', () => {
    render(<PipelineStepper status="CREATED" />)
    expect(screen.getByRole('listitem', { name: /Queued — current step/ })).toBeInTheDocument()
  })

  it('collapses READY_FOR_REVIEW, APPROVED, and FINAL onto the ready stage', () => {
    for (const status of ['READY_FOR_REVIEW', 'APPROVED', 'FINAL'] as const) {
      const { unmount } = render(<PipelineStepper status={status} />)
      expect(screen.getByRole('listitem', { name: /Ready — current step/ })).toBeInTheDocument()
      expect(screen.getByRole('listitem', { name: /Video — complete/ })).toBeInTheDocument()
      unmount()
    }
  })

  it('shows a distinct revision treatment at the ready stage for REVISION_REQUESTED', () => {
    render(<PipelineStepper status="REVISION_REQUESTED" />)

    const revisionStep = screen.getByRole('listitem', { name: /Ready — needs revision/ })
    expect(revisionStep).toHaveAttribute('aria-current', 'step')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('marks no stage as current and shows a failed banner for FAILED', () => {
    render(<PipelineStepper status="FAILED" />)

    expect(screen.queryByRole('listitem', { name: /current step/ })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/generation failed/i)
  })

  it('marks no stage as current and shows a cancelled banner for CANCELLED', () => {
    render(<PipelineStepper status="CANCELLED" />)

    expect(screen.queryByRole('listitem', { name: /current step/ })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(/cancelled/i)
  })

  it('uses currentStep to show Voiceover as its own current stage, since CampaignStatus has no GENERATING_VOICEOVER value', () => {
    render(<PipelineStepper status="GENERATING_IMAGES" currentStep="voiceover" />)

    expect(screen.getByRole('listitem', { name: /Images — complete/ })).toBeInTheDocument()
    const current = screen.getByRole('listitem', { name: /Voiceover — current step/ })
    expect(current).toHaveAttribute('aria-current', 'step')
    expect(screen.getByRole('listitem', { name: /Video — upcoming/ })).toBeInTheDocument()
  })

  it('falls back to the coarse status mapping when currentStep is not provided', () => {
    render(<PipelineStepper status="GENERATING_IMAGES" />)

    expect(screen.getByRole('listitem', { name: /Images — current step/ })).toBeInTheDocument()
  })
})
