import type { components } from '../../api/schema.gen'
import { SectionHeading } from './SectionHeading'
import type { AssetStatus } from './asset'
import styles from './StoryboardSection.module.css'

type Storyboard = components['schemas']['Storyboard']

export interface StoryboardSectionProps {
  storyboard: Storyboard | null | undefined
  status: AssetStatus
}

export function StoryboardSection({ storyboard, status }: StoryboardSectionProps) {
  const scenes = storyboard?.scenes ?? []

  return (
    <section className={styles.section} aria-label="Storyboard">
      <SectionHeading title="Storyboard" status={status} />
      {scenes.length > 0 ? (
        <div className={styles.grid}>
          {scenes.map((scene) => (
            <article key={scene.scene_number} className={styles.scene}>
              <div className={styles.sceneHeader}>
                <span className={styles.sceneNumber}>
                  Scene {String(scene.scene_number).padStart(2, '0')}
                </span>
                <span className={styles.sceneDuration}>{scene.duration_seconds}s</span>
              </div>
              <div className={styles.sceneField}>
                <span className={styles.sceneLabel}>Purpose</span>
                <p className={styles.sceneValue}>{scene.purpose}</p>
              </div>
              <div className={styles.sceneField}>
                <span className={styles.sceneLabel}>Narration</span>
                <p className={styles.sceneValue}>{scene.narration}</p>
              </div>
              {scene.text_overlay ? (
                <div className={styles.sceneField}>
                  <span className={styles.sceneLabel}>On-screen text</span>
                  <p className={styles.sceneValue}>{scene.text_overlay}</p>
                </div>
              ) : null}
              <div className={styles.sceneField}>
                <span className={styles.sceneLabel}>Visual direction</span>
                <p className={styles.sceneValue}>{scene.visual_prompt}</p>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <p className={styles.placeholder}>
          {status === 'failed'
            ? "Storyboard couldn't be generated for this campaign."
            : 'Storyboard scenes will appear here once generated.'}
        </p>
      )}
    </section>
  )
}
