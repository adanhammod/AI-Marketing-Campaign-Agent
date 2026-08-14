import type { components } from '../../api/schema.gen'
import { AlertIcon, CheckIcon } from '../creation/icons'
import styles from './PipelineStepper.module.css'

export type CampaignStatus = components['schemas']['CampaignStatus']
export type WorkflowStep = components['schemas']['WorkflowStep']

type PipelineStage =
  | 'queued'
  | 'strategy'
  | 'copy'
  | 'storyboard'
  | 'images'
  | 'voiceover'
  | 'video'
  | 'ready'

const STAGE_ORDER: PipelineStage[] = [
  'queued',
  'strategy',
  'copy',
  'storyboard',
  'images',
  'voiceover',
  'video',
  'ready',
]

const STAGE_LABEL: Record<PipelineStage, string> = {
  queued: 'Queued',
  strategy: 'Strategy',
  copy: 'Copy',
  storyboard: 'Storyboard',
  images: 'Images',
  voiceover: 'Voiceover',
  video: 'Video',
  ready: 'Ready',
}

// Coarse fallback used only when `currentStep` isn't available yet (e.g. the
// very first render before the API responds). CampaignStatus itself has no
// GENERATING_VOICEOVER value -- it jumps GENERATING_IMAGES -> RENDERING_VIDEO
// -- so this map alone can never distinguish the Voiceover stage. Real
// distinction comes from WORKFLOW_STEP_TO_STAGE below via `currentStep`.
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

// WorkflowStep is the fine-grained signal that actually distinguishes
// Voiceover from Images/Video. 'package' has no visible stage here --
// final packaging happens after approval, out of scope for this stepper.
const WORKFLOW_STEP_TO_STAGE: Partial<Record<WorkflowStep, PipelineStage>> = {
  strategy: 'strategy',
  copy: 'copy',
  storyboard: 'storyboard',
  images: 'images',
  voiceover: 'voiceover',
  video: 'video',
}

function resolveStage(status: CampaignStatus, currentStep?: WorkflowStep | null): PipelineStage | undefined {
  // Terminal "everything's done" statuses always win, regardless of which
  // step last ran.
  if (status === 'READY_FOR_REVIEW' || status === 'APPROVED' || status === 'FINAL') {
    return 'ready'
  }
  if (currentStep) {
    const mapped = WORKFLOW_STEP_TO_STAGE[currentStep]
    if (mapped) return mapped
  }
  return STATUS_STAGE[status]
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
  currentStep?: WorkflowStep | null
}

export function PipelineStepper({ status, currentStep }: PipelineStepperProps) {
  const isHalted = status === 'FAILED' || status === 'CANCELLED'
  const isRevision = status === 'REVISION_REQUESTED'
  const stage = isRevision ? 'ready' : resolveStage(status, currentStep)
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
