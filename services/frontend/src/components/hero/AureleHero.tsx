import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { ArrowRightIcon } from '../creation/icons'
import { createAureleHero, type AureleHeroHandle } from './aureleScene'
import { HeroFallback } from './HeroFallback'
import styles from './AureleHero.module.css'

const STAGE_LABELS: Record<string, string> = {
  BRIEF: 'Brief',
  STRATEGY: 'Strategy',
  COPY: 'Copy',
  STORYBOARD: 'Storyboard',
  IMAGES: 'Images',
  VOICEOVER: 'Voiceover',
  VIDEO: 'Video',
  REVIEW: 'Review',
}

function HeroCopy({ stage, settled }: { stage: string | null; settled: boolean }) {
  return (
    <div className={styles.overlay}>
      <div className={styles.headlineBlock}>
        <h1 className={styles.title}>One brief. Every asset. Perfectly in sync.</h1>
        <p className={styles.subtitle}>Turn one idea into a complete AI-generated campaign.</p>
      </div>

      {stage && (
        <p className={styles.stageChip} aria-live="polite">
          {STAGE_LABELS[stage] ?? stage}
        </p>
      )}

      {/* Held in the composition's reserved corner, clear of the central
          campaign display, so it never competes with the 3D scene. */}
      <div className={styles.footer}>
        <p className={styles.resolutionText} data-settled={settled}>
          AI does the work.
          <br />
          You have the final word.
        </p>

        {/* The real primary CTA — usable from the first frame, not gated on
            the 3D sequence settling; only its visual emphasis reacts to settle. */}
        <Link to="/campaigns/new" className={styles.cta} data-settled={settled}>
          Create your campaign
          <ArrowRightIcon className={styles.ctaIcon} />
        </Link>
      </div>
    </div>
  )
}

export function AureleHero() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const [stage, setStage] = useState<string | null>(null)
  const [settled, setSettled] = useState(false)
  const [hasError, setHasError] = useState(false)

  useEffect(() => {
    const canvas = canvasRef.current
    const root = rootRef.current
    if (!canvas || !root) return

    if (typeof window.WebGLRenderingContext === 'undefined') {
      setHasError(true)
      return
    }

    let handle: AureleHeroHandle | undefined
    try {
      handle = createAureleHero(canvas, root)
    } catch {
      setHasError(true)
      return
    }

    const unsubscribe = window.AURELE_HERO?.onStage((event) => setStage(event.stage))
    const onComplete = () => setSettled(true)
    root.addEventListener('aurele-complete', onComplete)

    return () => {
      unsubscribe?.()
      root.removeEventListener('aurele-complete', onComplete)
      handle?.dispose()
    }
  }, [])

  if (hasError) {
    return <HeroFallback />
  }

  return (
    <main className={styles.page}>
      <section className={styles.heroViewport}>
        <div ref={rootRef} className={styles.canvasRoot}>
          <canvas ref={canvasRef} className={styles.canvas} />
        </div>
        <HeroCopy stage={stage} settled={settled} />
      </section>
    </main>
  )
}
