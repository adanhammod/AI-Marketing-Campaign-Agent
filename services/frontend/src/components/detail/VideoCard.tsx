import { useState } from 'react'

import { SectionCard } from './SectionCard'
import type { AssetStatus } from './asset'
import styles from './VideoCard.module.css'

export interface VideoCardProps {
  videoUrl: string | null | undefined
  status: AssetStatus
}

export function VideoCard({ videoUrl, status }: VideoCardProps) {
  const [duration, setDuration] = useState<number | null>(null)

  return (
    <SectionCard title="Final Campaign Video" status={status}>
      {videoUrl ? (
        <div className={styles.body}>
          {duration !== null ? (
            <p className={styles.duration}>{duration.toFixed(1)} seconds</p>
          ) : null}
          <div className={styles.frame}>
            <video
              controls
              src={videoUrl}
              className={styles.player}
              onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
            >
              Your browser does not support the video element.
            </video>
          </div>
        </div>
      ) : (
        <p className={styles.placeholder}>
          {status === 'failed'
            ? "Video couldn't be generated for this campaign."
            : 'The final video will appear here once rendering is complete.'}
        </p>
      )}
    </SectionCard>
  )
}
