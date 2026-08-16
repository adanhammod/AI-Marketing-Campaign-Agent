import { Link, useNavigate } from 'react-router-dom'

import { AIOutputPreview } from '../components/creation/AIOutputPreview'
import { CampaignForm } from '../components/creation/CampaignForm'
import styles from './CreateCampaignPage.module.css'

export function CreateCampaignPage() {
  const navigate = useNavigate()

  return (
    <main className={styles.page}>
      <div className={styles.workspace}>
        <section className={styles.writeColumn}>
          <Link to="/" className={styles.backLink}>
            &larr; Back to home
          </Link>
          <span className={styles.eyebrow}>AI creative studio</span>
          <h1 className={styles.title}>Create campaign</h1>
          <p className={styles.tagline}>Brief in. Campaign out.</p>
          <CampaignForm onCreated={(campaignId) => navigate(`/campaigns/${campaignId}`)} />
        </section>

        <aside className={styles.watchColumn}>
          <AIOutputPreview />
        </aside>
      </div>
    </main>
  )
}
