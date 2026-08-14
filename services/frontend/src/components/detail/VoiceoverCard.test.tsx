import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { VoiceoverCard } from './VoiceoverCard'

describe('VoiceoverCard', () => {
  it('shows the real provider but no duration until metadata loads', () => {
    render(<VoiceoverCard audioUrl="https://cdn.example/v.mp3" provider="polly" status="ready" />)
    expect(screen.getByText('polly')).toBeInTheDocument()
    expect(screen.queryByText(/^\d+(\.\d+)?s$/)).not.toBeInTheDocument()
  })

  it('shows the real duration, measured client-side, once the audio metadata loads', () => {
    render(<VoiceoverCard audioUrl="https://cdn.example/v.mp3" provider="polly" status="ready" />)
    const audio = document.querySelector('audio') as HTMLAudioElement
    Object.defineProperty(audio, 'duration', { value: 15.8, configurable: true })
    fireEvent(audio, new Event('loadedmetadata'))
    expect(screen.getByText('15.8s')).toBeInTheDocument()
  })

  it('shows a placeholder and no audio element when no voiceover has been generated yet', () => {
    render(<VoiceoverCard audioUrl={null} status="pending" />)
    expect(screen.getByText('Voiceover audio will appear here once ready.')).toBeInTheDocument()
    expect(document.querySelector('audio')).toBeNull()
  })
})
