import { Link } from 'react-router-dom'

import { ArrowRightIcon } from '../creation/icons'
import styles from './AureleHero.module.css'

// Same shot used as a wall print / display screen inside the approved 3D
// scene (services/frontend/public/hero/aurele-shot-03-hero.webp) — reused
// here as real campaign imagery instead of decorative filler. Used both as
// the WebGL-failure fallback and as the Suspense loading state while the
// Three.js chunk streams in, so there is exactly one static-hero code path.
const FALLBACK_IMAGE_URL = '/hero/aurele-shot-03-hero.webp'

export function HeroFallback() {
  return (
    <main className={styles.page}>
      <section className={styles.heroViewport}>
        <div
          className={styles.fallback}
          style={{ backgroundImage: `url(${FALLBACK_IMAGE_URL})` }}
          role="img"
          aria-label="AURELE campaign board"
        />
        <div className={styles.overlay}>
          <div className={styles.headlineBlock}>
            <h1 className={styles.title}>One brief. Every asset. Perfectly in sync.</h1>
            <p className={styles.subtitle}>Turn one idea into a complete AI-generated campaign.</p>
          </div>

          <div className={styles.footer}>
            <p className={styles.resolutionText} data-settled="true">
              AI does the work.
              <br />
              You have the final word.
            </p>

            <Link to="/campaigns/new" className={styles.cta} data-settled="true">
              Create your campaign
              <ArrowRightIcon className={styles.ctaIcon} />
            </Link>
          </div>
        </div>
      </section>
    </main>
  )
}
