export interface CampaignFormValues {
  business_name: string
  product_or_service: string
  business_description: string
  campaign_goal: string
  platforms: string[]
  tone: string
  language: string
  target_audience: string
  key_message: string
  call_to_action: string
  brand_colors: string
}

export type CampaignFormErrors = Partial<Record<keyof CampaignFormValues, string>>

function requiredLength(
  errors: CampaignFormErrors,
  field: keyof CampaignFormValues,
  label: string,
  value: string,
  min: number,
  max: number,
): void {
  const length = value.trim().length
  if (length === 0) {
    errors[field] = `${label} is required.`
  } else if (length < min || length > max) {
    errors[field] = `${label} must be between ${min} and ${max} characters.`
  }
}

function optionalLength(
  errors: CampaignFormErrors,
  field: keyof CampaignFormValues,
  label: string,
  value: string,
  max: number,
): void {
  const length = value.trim().length
  if (length > max) {
    errors[field] = `${label} must be at most ${max} characters.`
  }
}

export function parseBrandColors(raw: string): string[] {
  return raw
    .split(',')
    .map((color) => color.trim())
    .filter((color) => color.length > 0)
}

export function validateCampaignForm(values: CampaignFormValues): CampaignFormErrors {
  const errors: CampaignFormErrors = {}

  requiredLength(errors, 'business_name', 'Business name', values.business_name, 2, 120)
  requiredLength(
    errors,
    'product_or_service',
    'Product or service',
    values.product_or_service,
    2,
    200,
  )
  requiredLength(
    errors,
    'business_description',
    'Business description',
    values.business_description,
    20,
    2000,
  )
  requiredLength(errors, 'campaign_goal', 'Campaign goal', values.campaign_goal, 3, 300)
  requiredLength(errors, 'tone', 'Tone', values.tone, 2, 80)
  requiredLength(errors, 'language', 'Language', values.language, 2, 20)

  if (values.platforms.length === 0) {
    errors.platforms = 'Select at least one platform.'
  } else if (values.platforms.length > 5) {
    errors.platforms = 'Select at most 5 platforms.'
  }

  optionalLength(errors, 'target_audience', 'Target audience', values.target_audience, 1000)
  optionalLength(errors, 'key_message', 'Key message', values.key_message, 500)
  optionalLength(errors, 'call_to_action', 'Call to action', values.call_to_action, 200)

  if (parseBrandColors(values.brand_colors).length > 5) {
    errors.brand_colors = 'Enter at most 5 brand colors.'
  }

  return errors
}
