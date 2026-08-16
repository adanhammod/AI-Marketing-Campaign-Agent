import { useRef } from 'react'
import { Link } from 'react-router-dom'
import { gsap } from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import { useGSAP } from '@gsap/react'

import { ArrowRightIcon, CheckIcon } from '../creation/icons'
import campaignImage1 from '../../assets/fixtures/campaign-image-1.svg'
import campaignImage2 from '../../assets/fixtures/campaign-image-2.svg'
import campaignImage3 from '../../assets/fixtures/campaign-image-3.svg'
import styles from './ScrollHero.module.css'

gsap.registerPlugin(ScrollTrigger)

const DESKTOP_QUERY = '(min-width: 960px)'

// One persistent Campaign Canvas, driven by a single timeline. Every stage
// below is a transformation of the same DOM nodes, not a screen swap.
const BRIEF_START = 0
const STRATEGY_START = 1.0
const COPY_START = 2.0
const STORYBOARD_START = 3.2
const IMAGES_START = 4.4
const VOICEOVER_START = 6.4
const VIDEO_START = 7.2
const REVIEW_START = 9.2
const RESOLUTION_START = 10.2

const STAGE_CAPTIONS: { key: string; start: number; label: string }[] = [
  { key: 'brief', start: BRIEF_START, label: 'Brief' },
  { key: 'strategy', start: STRATEGY_START, label: 'Strategy' },
  { key: 'copy', start: COPY_START, label: 'Copy' },
  { key: 'storyboard', start: STORYBOARD_START, label: 'Storyboard' },
  { key: 'images', start: IMAGES_START, label: 'Images' },
  { key: 'voiceover', start: VOICEOVER_START, label: 'Voiceover' },
  { key: 'video', start: VIDEO_START, label: 'Video' },
  { key: 'review', start: REVIEW_START, label: 'Review' },
]

function activeStageLabel(time: number): string {
  let label = STAGE_CAPTIONS[0].label
  for (const stage of STAGE_CAPTIONS) {
    if (time >= stage.start) label = stage.label
  }
  return label
}

export function ScrollHero() {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const stageCaptionRef = useRef<HTMLParagraphElement>(null)
  const reinforcementRef = useRef<HTMLParagraphElement>(null)

  const briefLinesRef = useRef<HTMLDivElement>(null)
  const ruleRef = useRef<HTMLDivElement>(null)
  const skeletonRef = useRef<HTMLDivElement>(null)
  const skeletonHeadlineRef = useRef<HTMLSpanElement>(null)
  const skeletonSubheadRef = useRef<HTMLSpanElement>(null)
  const skeletonCtaRef = useRef<HTMLSpanElement>(null)
  const copyStackRef = useRef<HTMLDivElement>(null)
  const copyHeadlineRef = useRef<HTMLParagraphElement>(null)
  const copySubheadRef = useRef<HTMLParagraphElement>(null)
  const copyCtaRef = useRef<HTMLSpanElement>(null)

  const panelRefs = useRef<(HTMLDivElement | null)[]>([])
  const dividerRefs = useRef<(HTMLDivElement | null)[]>([])
  const mergedFrameRef = useRef<HTMLImageElement>(null)

  const waveformRef = useRef<HTMLDivElement>(null)
  const chromeRef = useRef<HTMLDivElement>(null)

  const reviewEdgeRef = useRef<HTMLDivElement>(null)

  const resolutionTextRef = useRef<HTMLParagraphElement>(null)
  const reviewPreviewRef = useRef<HTMLDivElement>(null)
  const ctaRef = useRef<HTMLAnchorElement>(null)

  useGSAP(
    () => {
      const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      if (reduceMotion) {
        return
      }

      const isDesktop = window.matchMedia(DESKTOP_QUERY).matches
      const panels = panelRefs.current.filter((el): el is HTMLDivElement => Boolean(el))
      const dividers = dividerRefs.current.filter((el): el is HTMLDivElement => Boolean(el))

      // Constant tilt: set once so later scale/opacity tweens never clobber it
      // (GSAP tracks rotation/scale/position as independent transform components).
      gsap.set(canvasRef.current, {
        transformPerspective: 1200,
        rotationY: isDesktop ? -8 : 0,
        rotationX: isDesktop ? 3 : 0,
        opacity: 0,
        scale: 0.92,
      })

      gsap.set([briefLinesRef.current, skeletonRef.current, reinforcementRef.current], { opacity: 0 })
      gsap.set(ruleRef.current, { scaleX: 0, backgroundColor: 'var(--color-indigo)' })
      gsap.set(copyStackRef.current, { opacity: 0, top: '6%' })
      gsap.set(panels, { opacity: 0 })
      gsap.set(dividers, { opacity: 0, scaleY: 0 })
      gsap.set(mergedFrameRef.current, { opacity: 0 })
      gsap.set(waveformRef.current, { opacity: 0 })
      gsap.set(chromeRef.current, { opacity: 0 })
      gsap.set(reviewEdgeRef.current, { opacity: 0, scale: 1.03 })
      gsap.set(resolutionTextRef.current, { opacity: 0, y: 20 })
      gsap.set(reviewPreviewRef.current, { opacity: 0, y: 20 })
      gsap.set(ctaRef.current, { opacity: 0, y: 20, pointerEvents: 'none' })

      const tl = gsap.timeline({ paused: true })
      tl.eventCallback('onUpdate', () => {
        if (stageCaptionRef.current) {
          stageCaptionRef.current.textContent = activeStageLabel(tl.time())
        }
      })

      // Brief — the canvas arrives, the brief reads as raw input.
      tl.to(canvasRef.current, { opacity: 1, scale: 1, duration: 0.7, ease: 'back.out(1.6)' }, BRIEF_START)
      tl.to(briefLinesRef.current, { opacity: 1, duration: 0.4, ease: 'power1.out' }, BRIEF_START + 0.3)

      // Brief -> Strategy — a rule sweeps in; where it passes, structure appears.
      tl.to(ruleRef.current, { scaleX: 1, duration: 0.5, ease: 'power2.out' }, STRATEGY_START)
      tl.to(briefLinesRef.current, { opacity: 0, duration: 0.35 }, STRATEGY_START + 0.15)
      tl.to(skeletonRef.current, { opacity: 1, duration: 0.35 }, STRATEGY_START + 0.3)

      // Strategy -> Copy — the skeleton resolves into real copy: width/alignment
      // adjust first, then the structure fades as the real text settles in.
      tl.to(
        [skeletonHeadlineRef.current, skeletonSubheadRef.current],
        { scaleX: 0.85, duration: 0.4, ease: 'power2.inOut' },
        COPY_START,
      )
      tl.to(skeletonCtaRef.current, { scaleX: 1.25, duration: 0.4, ease: 'power2.inOut' }, COPY_START)
      tl.to(skeletonRef.current, { opacity: 0, duration: 0.4 }, COPY_START + 0.35)
      tl.to(copyStackRef.current, { opacity: 1, duration: 0.3 }, COPY_START + 0.4)
      tl.fromTo(
        copyHeadlineRef.current,
        { y: 8, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.4, ease: 'power2.out' },
        COPY_START + 0.4,
      )
      tl.fromTo(
        copySubheadRef.current,
        { y: 8, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.4, ease: 'power2.out' },
        COPY_START + 0.5,
      )
      tl.fromTo(
        copyCtaRef.current,
        { y: 8, opacity: 0 },
        { y: 0, opacity: 1, duration: 0.4, ease: 'power2.out' },
        COPY_START + 0.6,
      )
      tl.to(ruleRef.current, { backgroundColor: 'var(--color-divider)', duration: 0.5 }, COPY_START + 0.3)

      // Copy -> Storyboard — the body opens; two dividers draw in; the header settles back.
      tl.to(dividers, { opacity: 1, scaleY: 1, duration: 0.4, stagger: 0.1, ease: 'power2.out' }, STORYBOARD_START)
      tl.to(copyStackRef.current, { opacity: 0.85, scale: 0.97, duration: 0.4 }, STORYBOARD_START)

      // Storyboard -> Images — panels fill in, left to right, the payoff beat.
      panels.forEach((panel, index) => {
        tl.fromTo(
          panel,
          { opacity: 0, scale: 1.05 },
          { opacity: 1, scale: 1, duration: 0.55, ease: 'power2.out' },
          IMAGES_START + index * 0.35,
        )
      })
      tl.to(reinforcementRef.current, { opacity: 1, duration: 0.4 }, IMAGES_START + 0.6)
      tl.to(reinforcementRef.current, { opacity: 0, duration: 0.4 }, VOICEOVER_START - 0.3)

      // Images -> Voiceover — an audio layer joins the composition.
      tl.to(waveformRef.current, { opacity: 1, duration: 0.35 }, VOICEOVER_START)

      // Voiceover -> Video — panels merge into one creative; copy resettles as
      // a lower third; the waveform becomes video chrome.
      tl.to(dividers, { opacity: 0, duration: 0.4 }, VIDEO_START)
      tl.to(mergedFrameRef.current, { opacity: 1, duration: 0.6, ease: 'power1.inOut' }, VIDEO_START)
      tl.to(panels, { opacity: 0, duration: 0.6 }, VIDEO_START + 0.1)
      tl.to(waveformRef.current, { opacity: 0, duration: 0.35 }, VIDEO_START)
      tl.to(chromeRef.current, { opacity: 1, duration: 0.4 }, VIDEO_START + 0.3)
      tl.to(
        copyStackRef.current,
        { top: '74%', scale: 1, opacity: 1, duration: 0.7, ease: 'power2.inOut' },
        VIDEO_START + 0.2,
      )

      // Video -> Review — the canvas locks in; a proof edge draws around it.
      tl.to(canvasRef.current, { scale: 1.015, duration: 0.25, ease: 'power2.out' }, REVIEW_START)
        .to(canvasRef.current, { scale: 1, duration: 0.4, ease: 'elastic.out(1, 0.6)' }, REVIEW_START + 0.25)
      tl.to(reviewEdgeRef.current, { opacity: 1, scale: 1, duration: 0.5, ease: 'power2.out' }, REVIEW_START)

      // Resolution — message, illustrative Approve/Revise, then the real CTA.
      tl.to(resolutionTextRef.current, { opacity: 1, y: 0, duration: 0.45, ease: 'power2.out' }, RESOLUTION_START)
        .to(
          reviewPreviewRef.current,
          { opacity: 1, y: 0, duration: 0.45, ease: 'power2.out' },
          RESOLUTION_START + 0.35,
        )
        .to(ctaRef.current, { opacity: 1, y: 0, duration: 0.45, ease: 'power2.out' }, RESOLUTION_START + 0.7)
        .set(ctaRef.current, { pointerEvents: 'auto' }, RESOLUTION_START + 0.7)

      ScrollTrigger.create({
        trigger: containerRef.current,
        start: 'top bottom',
        end: 'bottom top',
        onEnter: () => {
          if (tl.progress() < 1) tl.play()
        },
        onEnterBack: () => {
          if (tl.progress() < 1) tl.play()
        },
        onLeave: () => tl.pause(),
        onLeaveBack: () => tl.pause(),
      })
    },
    { scope: containerRef },
  )

  return (
    <main className={styles.page}>
      <div ref={containerRef} className={styles.stage}>
        <div className={styles.headlineBlock}>
          <p className={styles.eyebrow}>Campaign demo</p>
          <h1 className={styles.title} aria-label="Turn ideas into impact">
            <span className={styles.titleQuiet} aria-hidden="true">
              Turn ideas
            </span>
            <span className={styles.titleBold} aria-hidden="true">
              into impact
            </span>
          </h1>
          <p className={styles.subtitle}>From one brief to a complete AI-powered campaign</p>
          <p ref={reinforcementRef} className={styles.reinforcement}>
            One brief. Every asset. Perfectly in sync.
          </p>
        </div>

        <div className={styles.canvasPerspective}>
          <p ref={stageCaptionRef} className={styles.stageCaption}>
            Brief
          </p>

          <div ref={canvasRef} className={styles.canvas}>
            <div className={styles.canvasMat}>
              <div className={styles.zoneHeader}>
                <div ref={briefLinesRef} className={styles.briefLines}>
                  <p className={styles.briefTitle}>Summer Campaign</p>
                  <p>
                    <span>Audience</span> Young professionals
                  </p>
                  <p>
                    <span>Tone</span> Bold / energetic
                  </p>
                  <p>
                    <span>Goal</span> Launch awareness
                  </p>
                </div>

                <div ref={ruleRef} className={styles.structureRule} />

                <div ref={skeletonRef} className={styles.skeletonStack}>
                  <span ref={skeletonHeadlineRef} className={styles.skeletonHeadline} />
                  <span ref={skeletonSubheadRef} className={styles.skeletonSubhead} />
                  <span ref={skeletonCtaRef} className={styles.skeletonCta} />
                </div>
              </div>

              <div className={styles.zoneBody}>
                <div className={styles.panels}>
                  {[campaignImage1, campaignImage2, campaignImage3].map((src, index) => (
                    <div
                      key={src}
                      ref={(el) => {
                        panelRefs.current[index] = el
                      }}
                      className={styles.panel}
                    >
                      <img src={src} alt="" className={styles.panelImage} />
                    </div>
                  ))}
                </div>
                {[0, 1].map((index) => (
                  <div
                    key={index}
                    ref={(el) => {
                      dividerRefs.current[index] = el
                    }}
                    className={styles.panelDivider}
                    style={{ left: `${(index + 1) * 33.333}%` }}
                  />
                ))}
                <img ref={mergedFrameRef} src={campaignImage1} alt="" className={styles.mergedFrame} />
              </div>

              <div className={styles.zoneFooter}>
                <div ref={waveformRef} className={styles.waveform} aria-hidden="true">
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                  <span />
                </div>
                <div ref={chromeRef} className={styles.videoChrome} aria-hidden="true">
                  <span className={styles.playGlyph} />
                  <span className={styles.scrubTrack}>
                    <span className={styles.scrubFill} />
                  </span>
                </div>
              </div>

              <div ref={copyStackRef} className={styles.copyStack}>
                <p ref={copyHeadlineRef} className={styles.copyHeadline}>
                  Your summer, amplified.
                </p>
                <p ref={copySubheadRef} className={styles.copySubhead}>
                  Bold looks for bold plans.
                </p>
                <span ref={copyCtaRef} className={styles.copyCta}>
                  Shop the edit
                </span>
              </div>
            </div>

            <div ref={reviewEdgeRef} className={styles.reviewEdge} aria-hidden="true" />
          </div>
          <div className={styles.canvasShadow} aria-hidden="true" />
        </div>

        <div className={styles.resolution}>
          <p ref={resolutionTextRef} className={styles.resolutionText}>
            AI does the work. You have the final word.
          </p>

          <div ref={reviewPreviewRef} className={styles.reviewPreview} aria-hidden="true">
            <span className={styles.reviewChipApprove}>
              <CheckIcon className={styles.reviewChipIcon} />
              Approve
            </span>
            <span className={styles.reviewChipRevise}>Revise</span>
          </div>

          <Link ref={ctaRef} to="/campaigns/new" className={styles.cta}>
            Create your campaign
            <ArrowRightIcon className={styles.ctaIcon} />
          </Link>
        </div>
      </div>
    </main>
  )
}
