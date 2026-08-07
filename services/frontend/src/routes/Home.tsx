import { Link } from 'react-router-dom'

import { ArrowRightIcon } from '../components/creation/icons'
import { FloatingScrap } from '../components/creation/FloatingScrap'
import campaignImage1 from '../assets/fixtures/campaign-image-1.svg'
import campaignImage2 from '../assets/fixtures/campaign-image-2.svg'
import campaignImage3 from '../assets/fixtures/campaign-image-3.svg'
import styles from './Home.module.css'

export function Home() {
  return (
    <main className={styles.page}>
      <div className={styles.workspace}>
        <div className={styles.copyColumn}>
          <h1 className={styles.title}>Grow your business with AI-generated campaigns</h1>
          <p className={styles.subtitle}>
            Brief the AI once. It plans the strategy, writes the copy, and cuts the video &mdash;
            nothing goes out until you approve it.
          </p>
          <Link to="/campaigns/new" className={styles.cta}>
            Create campaign
            <ArrowRightIcon className={styles.ctaIcon} />
          </Link>
        </div>

        <div className={styles.teaserColumn}>
          <FloatingScrap tone="indigo" rotate={-1.5} className={styles.teaserCard}>
            <div className={styles.skeletonBars} aria-hidden="true">
              <span />
              <span />
              <span />
              <span />
            </div>
            <h3 className={styles.teaserLabel}>Marketing Strategy</h3>
          </FloatingScrap>

          <FloatingScrap tone="emerald" rotate={1.5} className={styles.teaserCard}>
            <div className={styles.shotGrid} aria-hidden="true">
              <span />
              <span />
              <span />
            </div>
            <h3 className={styles.teaserLabel}>Storyboard</h3>
          </FloatingScrap>

          <FloatingScrap tone="coral" rotate={-1} className={styles.teaserCard}>
            <div className={styles.imageStrip}>
              <img
                src={campaignImage1}
                alt="Example: product hero visual for a generated campaign"
                className={styles.imageThumb}
              />
              <img
                src={campaignImage2}
                alt="Example: lifestyle scene visual for a generated campaign"
                className={styles.imageThumb}
              />
              <img
                src={campaignImage3}
                alt="Example: abstract pattern visual for a generated campaign"
                className={styles.imageThumb}
              />
            </div>
            <div className={styles.teaserLabelRow}>
              <h3 className={styles.teaserLabel}>Images</h3>
              <span className={styles.exampleBadge}>Example output</span>
            </div>
          </FloatingScrap>
        </div>
      </div>
    </main>
  )
}
