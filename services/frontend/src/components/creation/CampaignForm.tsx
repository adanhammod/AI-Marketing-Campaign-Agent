import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from 'react'

import { ApiError } from '../../api/client'
import { useCreateCampaign } from '../../api/queries/campaigns'
import {
  parseBrandColors,
  validateCampaignForm,
  type CampaignFormValues,
} from './validateCampaignForm'

const PLATFORM_OPTIONS = ['Instagram', 'TikTok', 'Facebook', 'YouTube', 'LinkedIn']

const EMPTY_VALUES: CampaignFormValues = {
  business_name: '',
  product_or_service: '',
  business_description: '',
  campaign_goal: '',
  platforms: [],
  tone: '',
  language: '',
  target_audience: '',
  key_message: '',
  call_to_action: '',
  brand_colors: '',
}

function extractErrorMessage(error: ApiError): string {
  const body = error.body
  if (
    body &&
    typeof body === 'object' &&
    'error' in body &&
    body.error &&
    typeof body.error === 'object' &&
    'message' in body.error &&
    typeof (body.error as { message: unknown }).message === 'string'
  ) {
    return (body.error as { message: string }).message
  }
  return 'Something went wrong while creating the campaign. Please try again.'
}

interface CampaignFormProps {
  onCreated: (campaignId: string) => void
}

export function CampaignForm({ onCreated }: CampaignFormProps) {
  const [values, setValues] = useState<CampaignFormValues>(EMPTY_VALUES)
  const [submitted, setSubmitted] = useState(false)
  const [apiError, setApiError] = useState<ApiError | null>(null)
  const formRef = useRef<HTMLFormElement | null>(null)
  const createCampaign = useCreateCampaign()

  const errors = submitted ? validateCampaignForm(values) : {}

  useEffect(() => {
    if (submitted && Object.keys(errors).length > 0) {
      formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submitted, values])

  function updateField(field: keyof CampaignFormValues) {
    return (event: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
      const fieldValue = event.target.value
      setValues((prev) => ({ ...prev, [field]: fieldValue }))
    }
  }

  function togglePlatform(platform: string) {
    setValues((prev) => ({
      ...prev,
      platforms: prev.platforms.includes(platform)
        ? prev.platforms.filter((p) => p !== platform)
        : [...prev.platforms, platform],
    }))
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitted(true)
    const validationErrors = validateCampaignForm(values)
    if (Object.keys(validationErrors).length > 0) {
      return
    }

    setApiError(null)
    try {
      const response = await createCampaign.mutateAsync({
        business_name: values.business_name.trim(),
        product_or_service: values.product_or_service.trim(),
        business_description: values.business_description.trim(),
        campaign_goal: values.campaign_goal.trim(),
        platforms: values.platforms.map((platform) => platform.toLowerCase()),
        tone: values.tone.trim(),
        language: values.language.trim(),
        target_audience: values.target_audience.trim() || undefined,
        key_message: values.key_message.trim() || undefined,
        call_to_action: values.call_to_action.trim() || undefined,
        brand_colors: parseBrandColors(values.brand_colors),
      })
      onCreated(response.campaign_id)
    } catch (error) {
      setApiError(
        error instanceof ApiError
          ? error
          : new ApiError(0, 'Unable to reach the server. Please try again.'),
      )
    }
  }

  return (
    <form ref={formRef} onSubmit={(event) => void handleSubmit(event)} noValidate>
      {apiError ? <p role="alert">{extractErrorMessage(apiError)}</p> : null}

      <div>
        <label htmlFor="business_name">Business name</label>
        <input
          id="business_name"
          value={values.business_name}
          onChange={updateField('business_name')}
          aria-invalid={errors.business_name ? 'true' : undefined}
          aria-describedby={errors.business_name ? 'business_name-error' : undefined}
        />
        {errors.business_name ? (
          <p role="alert" id="business_name-error">
            {errors.business_name}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="product_or_service">Product or service</label>
        <input
          id="product_or_service"
          value={values.product_or_service}
          onChange={updateField('product_or_service')}
          aria-invalid={errors.product_or_service ? 'true' : undefined}
          aria-describedby={errors.product_or_service ? 'product_or_service-error' : undefined}
        />
        {errors.product_or_service ? (
          <p role="alert" id="product_or_service-error">
            {errors.product_or_service}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="business_description">Business description</label>
        <textarea
          id="business_description"
          value={values.business_description}
          onChange={updateField('business_description')}
          aria-invalid={errors.business_description ? 'true' : undefined}
          aria-describedby={errors.business_description ? 'business_description-error' : undefined}
        />
        {errors.business_description ? (
          <p role="alert" id="business_description-error">
            {errors.business_description}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="campaign_goal">Campaign goal</label>
        <input
          id="campaign_goal"
          value={values.campaign_goal}
          onChange={updateField('campaign_goal')}
          aria-invalid={errors.campaign_goal ? 'true' : undefined}
          aria-describedby={errors.campaign_goal ? 'campaign_goal-error' : undefined}
        />
        {errors.campaign_goal ? (
          <p role="alert" id="campaign_goal-error">
            {errors.campaign_goal}
          </p>
        ) : null}
      </div>

      <fieldset aria-invalid={errors.platforms ? 'true' : undefined}>
        <legend>Platforms</legend>
        {PLATFORM_OPTIONS.map((platform) => {
          const id = `platform-${platform.toLowerCase()}`
          return (
            <div key={platform}>
              <input
                type="checkbox"
                id={id}
                checked={values.platforms.includes(platform)}
                onChange={() => togglePlatform(platform)}
              />
              <label htmlFor={id}>{platform}</label>
            </div>
          )
        })}
        {errors.platforms ? (
          <p role="alert" id="platforms-error">
            {errors.platforms}
          </p>
        ) : null}
      </fieldset>

      <div>
        <label htmlFor="tone">Tone</label>
        <input
          id="tone"
          value={values.tone}
          onChange={updateField('tone')}
          aria-invalid={errors.tone ? 'true' : undefined}
          aria-describedby={errors.tone ? 'tone-error' : undefined}
        />
        {errors.tone ? (
          <p role="alert" id="tone-error">
            {errors.tone}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="language">Language</label>
        <input
          id="language"
          placeholder="e.g. en-US"
          value={values.language}
          onChange={updateField('language')}
          aria-invalid={errors.language ? 'true' : undefined}
          aria-describedby={errors.language ? 'language-error' : undefined}
        />
        {errors.language ? (
          <p role="alert" id="language-error">
            {errors.language}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="target_audience">Target audience (optional)</label>
        <textarea
          id="target_audience"
          value={values.target_audience}
          onChange={updateField('target_audience')}
          aria-invalid={errors.target_audience ? 'true' : undefined}
          aria-describedby={errors.target_audience ? 'target_audience-error' : undefined}
        />
        {errors.target_audience ? (
          <p role="alert" id="target_audience-error">
            {errors.target_audience}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="key_message">Key message (optional)</label>
        <input
          id="key_message"
          value={values.key_message}
          onChange={updateField('key_message')}
          aria-invalid={errors.key_message ? 'true' : undefined}
          aria-describedby={errors.key_message ? 'key_message-error' : undefined}
        />
        {errors.key_message ? (
          <p role="alert" id="key_message-error">
            {errors.key_message}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="call_to_action">Call to action (optional)</label>
        <input
          id="call_to_action"
          value={values.call_to_action}
          onChange={updateField('call_to_action')}
          aria-invalid={errors.call_to_action ? 'true' : undefined}
          aria-describedby={errors.call_to_action ? 'call_to_action-error' : undefined}
        />
        {errors.call_to_action ? (
          <p role="alert" id="call_to_action-error">
            {errors.call_to_action}
          </p>
        ) : null}
      </div>

      <div>
        <label htmlFor="brand_colors">Brand colors (optional)</label>
        <input
          id="brand_colors"
          placeholder="e.g. #ff0000, #00ff00"
          value={values.brand_colors}
          onChange={updateField('brand_colors')}
          aria-invalid={errors.brand_colors ? 'true' : undefined}
          aria-describedby={errors.brand_colors ? 'brand_colors-error' : undefined}
        />
        {errors.brand_colors ? (
          <p role="alert" id="brand_colors-error">
            {errors.brand_colors}
          </p>
        ) : null}
      </div>

      <button type="submit" disabled={createCampaign.isPending}>
        Create campaign
      </button>
    </form>
  )
}
