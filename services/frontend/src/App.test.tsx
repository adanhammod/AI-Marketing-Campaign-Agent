import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import App from './App'

describe('App', () => {
  it('renders without crashing', () => {
    render(<App />)
    expect(
      screen.getByRole('heading', { name: /grow your business with ai-generated campaigns/i }),
    ).toBeInTheDocument()
  })
})
