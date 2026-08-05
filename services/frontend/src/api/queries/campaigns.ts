import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'

import { apiClient, unwrap } from '../client'
import type { components } from '../schema.gen'

type CampaignListResponse = components['schemas']['CampaignListResponse']
type CampaignDetailResponse = components['schemas']['CampaignDetailResponse']
type CampaignCreationRequest = components['schemas']['CampaignCreationRequest']
type CampaignCreationAcceptedResponse = components['schemas']['CampaignCreationAcceptedResponse']

interface CampaignListParams {
  offset?: number
  limit?: number
}

export function useCampaignList(
  params: CampaignListParams = {},
): UseQueryResult<CampaignListResponse> {
  const { offset = 0, limit } = params

  return useQuery({
    queryKey: ['campaigns', { offset, limit }],
    queryFn: () =>
      unwrap(
        apiClient.GET('/api/v1/campaigns', {
          params: { query: { offset, limit } },
        }),
      ),
  })
}

export function useCampaignDetail(campaignId: string): UseQueryResult<CampaignDetailResponse> {
  return useQuery({
    queryKey: ['campaign', campaignId],
    queryFn: () =>
      unwrap(
        apiClient.GET('/api/v1/campaigns/{campaign_id}', {
          params: { path: { campaign_id: campaignId } },
        }),
      ),
  })
}

export function useCreateCampaign(): UseMutationResult<
  CampaignCreationAcceptedResponse,
  Error,
  CampaignCreationRequest
> {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (body: CampaignCreationRequest) =>
      unwrap(
        apiClient.POST('/api/v1/campaigns', {
          params: { header: { 'Idempotency-Key': crypto.randomUUID() } },
          body,
        }),
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['campaigns'] })
    },
  })
}
