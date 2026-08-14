import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseMutationResult, UseQueryResult } from '@tanstack/react-query'

import { apiClient, unwrap } from '../client'
import type { components } from '../schema.gen'

type CampaignListResponse = components['schemas']['CampaignListResponse']
type CampaignDetailResponse = components['schemas']['CampaignDetailResponse']
type CampaignArtifactsResponse = components['schemas']['CampaignArtifactsResponse']
type CampaignCreationRequest = components['schemas']['CampaignCreationRequest']
type CampaignCreationAcceptedResponse = components['schemas']['CampaignCreationAcceptedResponse']
type CampaignStatus = components['schemas']['CampaignStatus']

// Statuses where nothing further will change for this specific campaign
// version without a human action (approve/revise/retry/cancel) -- none of
// which are wired up in the UI yet, so polling stops here rather than
// running forever. REVISION_REQUESTED is included: a revision spawns a
// *new* campaign_version, so this version's own detail record is done
// changing from this point on.
const TERMINAL_STATUSES = new Set<CampaignStatus>([
  'READY_FOR_REVIEW',
  'REVISION_REQUESTED',
  'APPROVED',
  'FINAL',
  'FAILED',
  'CANCELLED',
])

export function isTerminalCampaignStatus(status: CampaignStatus): boolean {
  return TERMINAL_STATUSES.has(status)
}

// Campaign generation runs asynchronously via SQS + worker; there's no
// backend-provided polling cadence, so this is a chosen default balancing
// responsiveness against request volume for a monitoring page.
const POLL_INTERVAL_MS = 5000

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
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && isTerminalCampaignStatus(status) ? false : POLL_INTERVAL_MS
    },
  })
}

interface UseCampaignArtifactsOptions {
  /** Poll only while the campaign is still generating; defaults to true. */
  pollWhileInProgress?: boolean
  /** Skip fetching until the caller is ready (e.g. campaign detail loaded); defaults to true. */
  enabled?: boolean
}

export function useCampaignArtifacts(
  campaignId: string,
  options: UseCampaignArtifactsOptions = {},
): UseQueryResult<CampaignArtifactsResponse> {
  const { pollWhileInProgress = true, enabled = true } = options

  return useQuery({
    queryKey: ['campaign', campaignId, 'artifacts'],
    queryFn: () =>
      unwrap(
        apiClient.GET('/api/v1/campaigns/{campaign_id}/artifacts', {
          params: { path: { campaign_id: campaignId } },
        }),
      ),
    enabled,
    refetchInterval: pollWhileInProgress ? POLL_INTERVAL_MS : false,
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
