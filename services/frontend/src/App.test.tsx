import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App', () => {
  it('renders without crashing', async () => {
    render(<App />)
    expect(
      await screen.findByRole('heading', { name: 'One brief. Every asset. Perfectly in sync.' }),
    ).toBeInTheDocument()
  })
})
