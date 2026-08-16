import { FloatingScrap } from './FloatingScrap'
import styles from './AIOutputPreview.module.css'
import { CopyIcon, VideoIcon } from './icons'

function StatusBadge() {
  return <span className={styles.statusBadge}>Coming after generation</span>
}

export function AIOutputPreview() {
  return (
    <div className={styles.grid} role="list">
      <FloatingScrap tone="indigo" span={2} rotate={-1} role="listitem" className={styles.card}>
        <div className={styles.skeletonBars} aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
        </div>
        <div className={styles.cardHeader}>
          <h3 className={styles.cardTitle}>Marketing Strategy</h3>
          <StatusBadge />
        </div>
        <p className={styles.cardDescription}>Positioning, audience, and channel plan.</p>
      </FloatingScrap>

      <FloatingScrap tone="sky" span={2} rotate={1} role="listitem" className={styles.card}>
        <div className={styles.captionDraft} aria-hidden="true">
          <CopyIcon className={styles.captionGlyph} />
          <div className={styles.captionBars}>
            <span />
            <span />
            <span />
          </div>
        </div>
        <div className={styles.cardHeader}>
          <h3 className={styles.cardTitle}>Social Copy</h3>
          <StatusBadge />
        </div>
        <p className={styles.cardDescription}>On-brand captions for every platform.</p>
      </FloatingScrap>

      <FloatingScrap tone="emerald" rotate={-1.5} role="listitem" className={styles.card}>
        <div className={styles.shotGrid} aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className={styles.cardHeader}>
          <h3 className={styles.cardTitle}>Storyboard</h3>
          <StatusBadge />
        </div>
        <p className={styles.cardDescription}>Shot-by-shot breakdown.</p>
      </FloatingScrap>

      <FloatingScrap tone="coral" rotate={1.5} role="listitem" className={styles.card}>
        <div className={styles.imageCanvas} aria-hidden="true">
          <span className={styles.imageCanvasBase} />
          <span className={styles.imageCanvasHover} />
        </div>
        <div className={styles.cardHeader}>
          <h3 className={styles.cardTitle}>Images</h3>
          <StatusBadge />
        </div>
        <p className={styles.cardDescription}>Visuals matched to your brand.</p>
      </FloatingScrap>

      <FloatingScrap tone="amber" rotate={-1} role="listitem" className={styles.card}>
        <div className={styles.waveform} aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
          <span />
        </div>
        <div className={styles.cardHeader}>
          <h3 className={styles.cardTitle}>Voiceover</h3>
          <StatusBadge />
        </div>
        <p className={styles.cardDescription}>AI-narrated audio in your voice.</p>
      </FloatingScrap>

      <FloatingScrap tone="emerald" span={2} rotate={0.5} role="listitem" className={styles.card}>
        <div className={styles.videoFrame} aria-hidden="true">
          <VideoIcon className={styles.videoPlay} />
        </div>
        <div className={styles.cardHeader}>
          <h3 className={styles.cardTitle}>Final Video</h3>
          <StatusBadge />
        </div>
        <p className={styles.cardDescription}>Assembled, polished, ready to publish.</p>
      </FloatingScrap>
    </div>
  )
}
