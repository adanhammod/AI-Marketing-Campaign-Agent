import { Link } from 'react-router-dom'

import type { components } from '../../api/schema.gen'
import styles from './CampaignHeader.module.css'

type CampaignStatus = components['schemas']['CampaignStatus']

type StatusBucket = 'queued' | 'generating' | 'review' | 'done' | 'failed' | 'cancelled'

const STATUS_BUCKET: Record<CampaignStatus, StatusBucket> = {
  CREATED: 'queued',
  QUEUED: 'queued',
  GENERATING_STRATEGY: 'generating',
  GENERATING_COPY: 'generating',
  GENERATING_STORYBOARD: 'generating',
  GENERATING_IMAGES: 'generating',
  RENDERING_VIDEO: 'generating',
  READY_FOR_REVIEW: 'review',
  REVISION_REQUESTED: 'generating',
  APPROVED: 'done',
  FINAL: 'done',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
}

const STATUS_LABEL: Record<CampaignStatus, string> = {
  CREATED: 'Created',
  QUEUED: 'Queued',
  GENERATING_STRATEGY: 'Generating strategy',
  GENERATING_COPY: 'Generating copy',
  GENERATING_STORYBOARD: 'Generating storyboard',
  GENERATING_IMAGES: 'Generating images',
  RENDERING_VIDEO: 'Rendering video',
  READY_FOR_REVIEW: 'Ready for review',
  REVISION_REQUESTED: 'Revision requested',
  APPROVED: 'Approved',
  FINAL: 'Final',
  FAILED: 'Failed',
  CANCELLED: 'Cancelled',
}

const RELATIVE_DIVISIONS: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 60 * 60 * 24 * 365],
  ['month', 60 * 60 * 24 * 30],
  ['day', 60 * 60 * 24],
  ['hour', 60 * 60],
  ['minute', 60],
]

function formatRelativeTime(iso: string, now = Date.now()): string {
  const diffSeconds = Math.round((new Date(iso).getTime() - now) / 1000)
  const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  for (const [unit, secondsInUnit] of RELATIVE_DIVISIONS) {
    if (Math.abs(diffSeconds) >= secondsInUnit) {
      return rtf.format(Math.round(diffSeconds / secondsInUnit), unit)
    }
  }
  return rtf.format(diffSeconds, 'second')
}

export interface CampaignHeaderProps {
  campaignId: string
  title?: string
  status?: CampaignStatus
  businessName?: string
  productOrService?: string
  updatedAt?: string
}

export function CampaignHeader({
  campaignId,
  title,
  status,
  businessName,
  productOrService,
  updatedAt,
}: CampaignHeaderProps) {
  const subtitle =
    businessName && productOrService ? `${businessName} — ${productOrService}` : undefined

  return (
    <header className={styles.header}>
      <Link to="/" className={styles.backLink}>
        &larr; Back to campaigns
      </Link>

      <div className={styles.eyebrowRow}>
        <span className={styles.eyebrow}>Monitoring campaign</span>
        {status ? (
          <span className={styles.statusBadge} data-bucket={STATUS_BUCKET[status]}>
            {STATUS_LABEL[status]}
          </span>
        ) : null}
      </div>

      <h1 className={styles.title}>{title ?? 'Loading campaign…'}</h1>
      {subtitle ? <p className={styles.subtitle}>{subtitle}</p> : null}

      <div className={styles.metaRow}>
        <span className={styles.campaignId}>Campaign {campaignId}</span>
        {updatedAt ? (
          <span className={styles.updated}>Updated {formatRelativeTime(updatedAt)}</span>
        ) : null}
      </div>
    </header>
  )
}
