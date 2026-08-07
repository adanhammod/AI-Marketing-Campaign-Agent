import type { CampaignStatus } from '../../routes/CampaignDetailPage.fixtures'
import { AlertIcon, CheckIcon } from '../creation/icons'
import styles from './PipelineStepper.module.css'

type PipelineStage = 'queued' | 'strategy' | 'copy' | 'storyboard' | 'images' | 'video' | 'ready'

const STAGE_ORDER: PipelineStage[] = [
  'queued',
  'strategy',
  'copy',
  'storyboard',
  'images',
  'video',
  'ready',
]

const STAGE_LABEL: Record<PipelineStage, string> = {
  queued: 'Queued',
  strategy: 'Strategy',
  copy: 'Copy',
  storyboard: 'Storyboard',
  images: 'Images',
  video: 'Video',
  ready: 'Ready',
}

// Collapses the API's granular CampaignStatus into the 7 stage groups shown
// here. REVISION_REQUESTED/FAILED/CANCELLED are handled separately below —
// they don't map onto a forward stage the way generation progress does.
const STATUS_STAGE: Partial<Record<CampaignStatus, PipelineStage>> = {
  CREATED: 'queued',
  QUEUED: 'queued',
  GENERATING_STRATEGY: 'strategy',
  GENERATING_COPY: 'copy',
  GENERATING_STORYBOARD: 'storyboard',
  GENERATING_IMAGES: 'images',
  RENDERING_VIDEO: 'video',
  READY_FOR_REVIEW: 'ready',
  APPROVED: 'ready',
  FINAL: 'ready',
}

type StepState = 'complete' | 'current' | 'revision' | 'upcoming' | 'halted'

const STATE_TEXT: Record<StepState, string> = {
  complete: 'complete',
  current: 'current step',
  revision: 'needs revision',
  upcoming: 'upcoming',
  halted: 'upcoming',
}

interface PipelineStepperProps {
  status: CampaignStatus
}

export function PipelineStepper({ status }: PipelineStepperProps) {
  const isHalted = status === 'FAILED' || status === 'CANCELLED'
  const isRevision = status === 'REVISION_REQUESTED'
  const stage = isRevision ? 'ready' : STATUS_STAGE[status]
  const currentIndex = isHalted || !stage ? -1 : STAGE_ORDER.indexOf(stage)

  return (
    <div className={styles.wrap}>
      <ol className={styles.track} aria-label="Campaign pipeline progress">
        {STAGE_ORDER.map((s, index) => {
          let state: StepState = 'upcoming'
          if (isHalted) {
            state = 'halted'
          } else if (index < currentIndex) {
            state = 'complete'
          } else if (index === currentIndex) {
            state = isRevision && s === 'ready' ? 'revision' : 'current'
          }

          return (
            <li
              key={s}
              className={styles.step}
              data-state={state}
              aria-current={state === 'current' || state === 'revision' ? 'step' : undefined}
              aria-label={`${STAGE_LABEL[s]} — ${STATE_TEXT[state]}`}
            >
              {index > 0 ? (
                <span
                  className={styles.line}
                  data-filled={index <= currentIndex && !isHalted}
                  aria-hidden="true"
                />
              ) : null}
              <span className={styles.node} aria-hidden="true">
                {state === 'complete' ? <CheckIcon className={styles.nodeIcon} /> : null}
                {state === 'revision' ? <AlertIcon className={styles.nodeIcon} /> : null}
              </span>
              <span className={styles.label}>{STAGE_LABEL[s]}</span>
            </li>
          )
        })}
      </ol>
      {isHalted ? (
        <p className={styles.haltedBanner} role="status">
          <AlertIcon className={styles.haltedIcon} />
          {status === 'FAILED'
            ? "Generation failed. Nothing you've approved is lost — you can retry from here."
            : 'This campaign was cancelled.'}
        </p>
      ) : null}
    </div>
  )
}
