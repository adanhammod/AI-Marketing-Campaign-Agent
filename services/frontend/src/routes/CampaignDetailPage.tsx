import { useParams } from 'react-router-dom'

import { isTerminalCampaignStatus, useCampaignArtifacts, useCampaignDetail } from '../api/queries/campaigns'
import type { components } from '../api/schema.gen'
import { AlertIcon } from '../components/creation/icons'
import { CampaignHeader } from '../components/detail/CampaignHeader'
import { CampaignOverview } from '../components/detail/CampaignOverview'
import { CopyCard } from '../components/detail/CopyCard'
import { ImageGallery, type GalleryImage } from '../components/detail/ImageGallery'
import { PipelineStepper } from '../components/detail/PipelineStepper'
import { ReviewActionsCard } from '../components/detail/ReviewActionsCard'
import { StoryboardSection } from '../components/detail/StoryboardSection'
import { StrategyCard } from '../components/detail/StrategyCard'
import { VideoCard } from '../components/detail/VideoCard'
import { VoiceoverCard } from '../components/detail/VoiceoverCard'
import type { AssetStatus } from '../components/detail/asset'
import styles from './CampaignDetailPage.module.css'

type CampaignDetailResponse = components['schemas']['CampaignDetailResponse']
type PublicArtifactReference = components['schemas']['PublicArtifactReference']
type WorkflowStep = components['schemas']['WorkflowStep']
type CampaignStatus = components['schemas']['CampaignStatus']

function deriveAssetStatus(params: {
  step: WorkflowStep
  isPresent: boolean
  currentStep?: WorkflowStep | null
  completedSteps?: WorkflowStep[]
  campaignStatus: CampaignStatus
  failedStep?: WorkflowStep | null
}): AssetStatus {
  const { step, isPresent, currentStep, completedSteps, campaignStatus, failedStep } = params
  if (isPresent) return 'ready'
  if (campaignStatus === 'FAILED' && failedStep === step) return 'failed'
  if (currentStep === step) return 'generating'
  if (completedSteps?.includes(step)) return 'ready'
  return 'pending'
}

export function CampaignDetailPage() {
  const { campaignId } = useParams<{ campaignId: string }>()

  if (!campaignId) {
    return null
  }

  return <CampaignDetail campaignId={campaignId} />
}

function CampaignDetail({ campaignId }: { campaignId: string }) {
  const detailQuery = useCampaignDetail(campaignId)
  const data = detailQuery.data
  const isInProgress = data ? !isTerminalCampaignStatus(data.status) : true
  const artifactsQuery = useCampaignArtifacts(campaignId, {
    pollWhileInProgress: isInProgress,
    enabled: detailQuery.isSuccess,
  })

  return (
    <main className={styles.page}>
      <div className={styles.container}>
        <CampaignHeader
          campaignId={campaignId}
          title={data?.title}
          status={data?.status}
          businessName={data?.brief.business_name}
          productOrService={data?.brief.product_or_service}
          updatedAt={data?.updated_at}
        />

        {detailQuery.isError ? (
          <div className={styles.errorBanner} role="alert">
            <AlertIcon className={styles.errorIcon} />
            <p className={styles.errorText}>
              We couldn&rsquo;t load this campaign. It may not exist, or something went wrong.
            </p>
          </div>
        ) : data ? (
          <CampaignDetailContent data={data} artifacts={artifactsQuery.data?.items} />
        ) : null}
      </div>
    </main>
  )
}

function CampaignDetailContent({
  data,
  artifacts,
}: {
  data: CampaignDetailResponse
  artifacts: PublicArtifactReference[] | undefined
}) {
  const statusFor = (step: WorkflowStep, isPresent: boolean) =>
    deriveAssetStatus({
      step,
      isPresent,
      currentStep: data.current_step,
      completedSteps: data.completed_steps,
      campaignStatus: data.status,
      failedStep: data.error?.workflow_step,
    })

  const knownArtifacts = data.artifacts ?? []
  const hasImages = knownArtifacts.some((artifact) => artifact.artifact_type === 'IMAGE')
  const hasAudio = knownArtifacts.some((artifact) => artifact.artifact_type === 'AUDIO')
  const hasVideo = knownArtifacts.some((artifact) => artifact.artifact_type === 'VIDEO')

  const signedImages: GalleryImage[] = (artifacts ?? [])
    .filter((artifact) => artifact.artifact_type === 'IMAGE' && artifact.download_url)
    .sort((a, b) => (a.scene_number ?? 0) - (b.scene_number ?? 0))
    .map((artifact) => ({
      url: artifact.download_url as string,
      sceneNumber: artifact.scene_number,
      attribution: artifact.attribution?.attribution_text,
    }))
  const audioArtifact = (artifacts ?? []).find((artifact) => artifact.artifact_type === 'AUDIO')
  const videoArtifact = (artifacts ?? []).find((artifact) => artifact.artifact_type === 'VIDEO')

  return (
    <div className={styles.sections}>
      <PipelineStepper status={data.status} currentStep={data.current_step} />
      <CampaignOverview brief={data.brief} />
      <div className={styles.twoCol}>
        <StrategyCard
          strategy={data.strategy}
          status={statusFor('strategy', data.strategy != null)}
        />
        <CopyCard copy={data.copy} status={statusFor('copy', data.copy != null)} />
      </div>
      <StoryboardSection
        storyboard={data.storyboard}
        status={statusFor('storyboard', data.storyboard != null)}
      />
      <ImageGallery images={signedImages} status={statusFor('images', hasImages)} />
      <VoiceoverCard
        audioUrl={audioArtifact?.download_url}
        provider={audioArtifact?.provider}
        status={statusFor('voiceover', hasAudio)}
      />
      <VideoCard videoUrl={videoArtifact?.download_url} status={statusFor('video', hasVideo)} />
      {data.status === 'READY_FOR_REVIEW' ? <ReviewActionsCard /> : null}
    </div>
  )
}
