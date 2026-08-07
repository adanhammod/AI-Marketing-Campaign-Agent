import { CheckIcon, CopyIcon, VideoIcon } from '../creation/icons'
import styles from './AssetStatusCard.module.css'
import {
  STATUS_LABEL,
  type AssetFixture,
  type AssetMetadata,
  type AssetStatus,
} from '../../routes/CampaignDetailPage.fixtures'
import campaignImage1 from '../../assets/fixtures/campaign-image-1.svg'
import campaignImage2 from '../../assets/fixtures/campaign-image-2.svg'
import campaignImage3 from '../../assets/fixtures/campaign-image-3.svg'

const READY_IMAGES = [
  { src: campaignImage1, alt: 'Product hero visual for the campaign' },
  { src: campaignImage2, alt: 'Lifestyle scene visual for the campaign' },
  { src: campaignImage3, alt: 'Abstract pattern visual for the campaign' },
]

function formatMetadata(metadata: AssetMetadata): string {
  switch (metadata.kind) {
    case 'images':
      return `${metadata.resolution} · ${metadata.aspectRatio}`
    case 'voiceover':
      return `${metadata.durationSeconds}s · ${metadata.wordCount} words`
    case 'copy':
      return metadata.platformCharacterCounts
        .map((entry) => `${entry.platform} ${entry.characters}`)
        .join(' · ')
    case 'strategy':
      return `${metadata.positioningPoints} positioning points`
  }
}

const TONE: Record<AssetFixture['key'], 'indigo' | 'sky' | 'emerald' | 'coral' | 'amber'> = {
  strategy: 'indigo',
  copy: 'sky',
  storyboard: 'emerald',
  images: 'coral',
  voiceover: 'amber',
  video: 'emerald',
}

const SPAN: Record<AssetFixture['key'], 1 | 2> = {
  strategy: 2,
  copy: 2,
  storyboard: 1,
  images: 1,
  voiceover: 1,
  video: 2,
}

function AssetShape({ assetKey, status }: { assetKey: AssetFixture['key']; status: AssetStatus }) {
  switch (assetKey) {
    case 'strategy':
      return (
        <div className={styles.skeletonBars}>
          <span />
          <span />
          <span />
          <span />
        </div>
      )
    case 'copy':
      return (
        <div className={styles.captionDraft}>
          <CopyIcon className={styles.captionGlyph} />
          <div className={styles.captionBars}>
            <span />
            <span />
            <span />
          </div>
        </div>
      )
    case 'storyboard':
      return (
        <div className={styles.shotGrid}>
          <span />
          <span />
          <span />
        </div>
      )
    case 'images':
      if (status === 'ready') {
        return (
          <div className={styles.imageCanvas} data-ready="true">
            {READY_IMAGES.map((image) => (
              <img key={image.src} src={image.src} alt={image.alt} className={styles.imageThumb} />
            ))}
          </div>
        )
      }
      return (
        <div className={styles.imageCanvas}>
          <span />
        </div>
      )
    case 'voiceover':
      return (
        <div className={styles.waveform}>
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
        </div>
      )
    case 'video':
      return (
        <div className={styles.videoFrame}>
          <VideoIcon className={styles.videoPlay} />
        </div>
      )
  }
}

interface AssetStatusCardProps {
  asset: AssetFixture
}

export function AssetStatusCard({ asset }: AssetStatusCardProps) {
  return (
    <div
      className={[styles.card, SPAN[asset.key] === 2 ? styles.wide : ''].filter(Boolean).join(' ')}
      data-tone={TONE[asset.key]}
      data-status={asset.status}
      role="listitem"
    >
      <span className={styles.readyBadge} aria-hidden="true">
        <CheckIcon className={styles.readyBadgeIcon} />
      </span>
      <div className={styles.shapeWrap}>
        <div aria-hidden={!(asset.key === 'images' && asset.status === 'ready')}>
          <AssetShape assetKey={asset.key} status={asset.status} />
        </div>
        {asset.status === 'ready' && asset.metadata ? (
          <p className={styles.revealPanel}>{formatMetadata(asset.metadata)}</p>
        ) : null}
      </div>
      <div className={styles.cardHeader}>
        <h3 className={styles.cardTitle}>{asset.title}</h3>
        <span className={styles.statusBadge} data-status={asset.status}>
          {STATUS_LABEL[asset.status]}
        </span>
      </div>
      <p className={styles.cardDescription}>{asset.description}</p>
    </div>
  )
}
