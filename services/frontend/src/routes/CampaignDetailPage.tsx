import { Link, useParams } from 'react-router-dom'

import { AssetStatusCard } from '../components/detail/AssetStatusCard'
import { PipelineStepper } from '../components/detail/PipelineStepper'
import { DEMO_ASSETS, DEMO_CAMPAIGN_TITLE, DEMO_STATUS } from './CampaignDetailPage.fixtures'
import styles from './CampaignDetailPage.module.css'

export function CampaignDetailPage() {
  const { campaignId } = useParams<{ campaignId: string }>()

  return (
    <main className={styles.page}>
      <div className={styles.container}>
        <Link to="/" className={styles.backLink}>
          &larr; Back to home
        </Link>

        <span className={styles.eyebrow}>Monitoring campaign</span>
        <h1 className={styles.title}>{DEMO_CAMPAIGN_TITLE}</h1>
        <h2 className={styles.campaignId}>Campaign {campaignId}</h2>
        <p className={styles.subtitle}>
          Strategy, copy, storyboard, images, voiceover, and video &mdash; each shown here as
          it&rsquo;s completed. Nothing is final until you approve it.
        </p>

        <PipelineStepper status={DEMO_STATUS} />

        <div className={styles.grid} role="list" aria-label="Campaign assets">
          {DEMO_ASSETS.map((asset) => (
            <AssetStatusCard key={asset.key} asset={asset} />
          ))}
        </div>
      </div>
    </main>
  )
}
