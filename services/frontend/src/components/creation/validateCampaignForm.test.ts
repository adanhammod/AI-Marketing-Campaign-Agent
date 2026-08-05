import { describe, expect, it } from 'vitest'

import { validateCampaignForm, type CampaignFormValues } from './validateCampaignForm'

function validValues(overrides: Partial<CampaignFormValues> = {}): CampaignFormValues {
  return {
    business_name: 'Acme Co',
    product_or_service: 'Widgets',
    business_description: 'We make high quality widgets for discerning customers everywhere.',
    campaign_goal: 'Drive summer sales',
    platforms: ['instagram'],
    tone: 'playful',
    language: 'en',
    target_audience: '',
    key_message: '',
    call_to_action: '',
    brand_colors: '',
    ...overrides,
  }
}

describe('validateCampaignForm', () => {
  it('returns no errors for fully valid values', () => {
    expect(validateCampaignForm(validValues())).toEqual({})
  })

  describe('business_name', () => {
    it('is required', () => {
      expect(validateCampaignForm(validValues({ business_name: '' }))).toHaveProperty(
        'business_name',
      )
    })
    it('rejects below the 2-char minimum', () => {
      expect(validateCampaignForm(validValues({ business_name: 'A' }))).toHaveProperty(
        'business_name',
      )
    })
    it('accepts exactly the 2-char minimum', () => {
      expect(validateCampaignForm(validValues({ business_name: 'Ab' }))).not.toHaveProperty(
        'business_name',
      )
    })
    it('accepts exactly the 120-char maximum', () => {
      expect(
        validateCampaignForm(validValues({ business_name: 'A'.repeat(120) })),
      ).not.toHaveProperty('business_name')
    })
    it('rejects above the 120-char maximum', () => {
      expect(validateCampaignForm(validValues({ business_name: 'A'.repeat(121) }))).toHaveProperty(
        'business_name',
      )
    })
  })

  describe('product_or_service', () => {
    it('is required', () => {
      expect(validateCampaignForm(validValues({ product_or_service: '' }))).toHaveProperty(
        'product_or_service',
      )
    })
    it('rejects below the 2-char minimum', () => {
      expect(validateCampaignForm(validValues({ product_or_service: 'A' }))).toHaveProperty(
        'product_or_service',
      )
    })
    it('rejects above the 200-char maximum', () => {
      expect(
        validateCampaignForm(validValues({ product_or_service: 'A'.repeat(201) })),
      ).toHaveProperty('product_or_service')
    })
    it('accepts exactly the 200-char maximum', () => {
      expect(
        validateCampaignForm(validValues({ product_or_service: 'A'.repeat(200) })),
      ).not.toHaveProperty('product_or_service')
    })
  })

  describe('business_description', () => {
    it('is required', () => {
      expect(validateCampaignForm(validValues({ business_description: '' }))).toHaveProperty(
        'business_description',
      )
    })
    it('rejects below the 20-char minimum', () => {
      expect(
        validateCampaignForm(validValues({ business_description: 'A'.repeat(19) })),
      ).toHaveProperty('business_description')
    })
    it('accepts exactly the 20-char minimum', () => {
      expect(
        validateCampaignForm(validValues({ business_description: 'A'.repeat(20) })),
      ).not.toHaveProperty('business_description')
    })
    it('accepts exactly the 2000-char maximum', () => {
      expect(
        validateCampaignForm(validValues({ business_description: 'A'.repeat(2000) })),
      ).not.toHaveProperty('business_description')
    })
    it('rejects above the 2000-char maximum', () => {
      expect(
        validateCampaignForm(validValues({ business_description: 'A'.repeat(2001) })),
      ).toHaveProperty('business_description')
    })
  })

  describe('campaign_goal', () => {
    it('is required', () => {
      expect(validateCampaignForm(validValues({ campaign_goal: '' }))).toHaveProperty(
        'campaign_goal',
      )
    })
    it('rejects below the 3-char minimum', () => {
      expect(validateCampaignForm(validValues({ campaign_goal: 'Ab' }))).toHaveProperty(
        'campaign_goal',
      )
    })
    it('rejects above the 300-char maximum', () => {
      expect(validateCampaignForm(validValues({ campaign_goal: 'A'.repeat(301) }))).toHaveProperty(
        'campaign_goal',
      )
    })
  })

  describe('tone', () => {
    it('is required', () => {
      expect(validateCampaignForm(validValues({ tone: '' }))).toHaveProperty('tone')
    })
    it('rejects below the 2-char minimum', () => {
      expect(validateCampaignForm(validValues({ tone: 'A' }))).toHaveProperty('tone')
    })
    it('rejects above the 80-char maximum', () => {
      expect(validateCampaignForm(validValues({ tone: 'A'.repeat(81) }))).toHaveProperty('tone')
    })
  })

  describe('language', () => {
    it('is required', () => {
      expect(validateCampaignForm(validValues({ language: '' }))).toHaveProperty('language')
    })
    it('rejects below the 2-char minimum', () => {
      expect(validateCampaignForm(validValues({ language: 'A' }))).toHaveProperty('language')
    })
    it('rejects above the 20-char maximum', () => {
      expect(validateCampaignForm(validValues({ language: 'A'.repeat(21) }))).toHaveProperty(
        'language',
      )
    })
  })

  describe('platforms', () => {
    it('requires at least one selection', () => {
      expect(validateCampaignForm(validValues({ platforms: [] }))).toHaveProperty('platforms')
    })
    it('accepts up to 5 selections', () => {
      expect(
        validateCampaignForm(validValues({ platforms: ['a', 'b', 'c', 'd', 'e'] })),
      ).not.toHaveProperty('platforms')
    })
    it('rejects more than 5 selections', () => {
      expect(
        validateCampaignForm(validValues({ platforms: ['a', 'b', 'c', 'd', 'e', 'f'] })),
      ).toHaveProperty('platforms')
    })
  })

  describe('optional fields', () => {
    it('target_audience is optional', () => {
      expect(validateCampaignForm(validValues({ target_audience: '' }))).not.toHaveProperty(
        'target_audience',
      )
    })
    it('rejects target_audience above the 1000-char maximum', () => {
      expect(
        validateCampaignForm(validValues({ target_audience: 'A'.repeat(1001) })),
      ).toHaveProperty('target_audience')
    })
    it('key_message is optional', () => {
      expect(validateCampaignForm(validValues({ key_message: '' }))).not.toHaveProperty(
        'key_message',
      )
    })
    it('rejects key_message above the 500-char maximum', () => {
      expect(validateCampaignForm(validValues({ key_message: 'A'.repeat(501) }))).toHaveProperty(
        'key_message',
      )
    })
    it('call_to_action is optional', () => {
      expect(validateCampaignForm(validValues({ call_to_action: '' }))).not.toHaveProperty(
        'call_to_action',
      )
    })
    it('rejects call_to_action above the 200-char maximum', () => {
      expect(validateCampaignForm(validValues({ call_to_action: 'A'.repeat(201) }))).toHaveProperty(
        'call_to_action',
      )
    })
  })

  describe('brand_colors', () => {
    it('is optional', () => {
      expect(validateCampaignForm(validValues({ brand_colors: '' }))).not.toHaveProperty(
        'brand_colors',
      )
    })
    it('accepts up to 5 comma-separated values', () => {
      expect(
        validateCampaignForm(validValues({ brand_colors: '#fff,#000,#111,#222,#333' })),
      ).not.toHaveProperty('brand_colors')
    })
    it('rejects more than 5 comma-separated values', () => {
      expect(
        validateCampaignForm(validValues({ brand_colors: '#fff,#000,#111,#222,#333,#444' })),
      ).toHaveProperty('brand_colors')
    })
    it('ignores blank entries created by stray commas when counting', () => {
      expect(validateCampaignForm(validValues({ brand_colors: '#fff,,#000,' }))).not.toHaveProperty(
        'brand_colors',
      )
    })
  })
})
