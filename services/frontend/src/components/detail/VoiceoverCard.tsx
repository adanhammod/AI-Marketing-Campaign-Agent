import { useState } from 'react'

import { SectionCard } from './SectionCard'
import type { AssetStatus } from './asset'
import styles from './VoiceoverCard.module.css'

export interface VoiceoverCardProps {
  audioUrl: string | null | undefined
  provider?: string | null
  status: AssetStatus
}

function formatDuration(seconds: number): string {
  return `${seconds.toFixed(1)}s`
}

export function VoiceoverCard({ audioUrl, provider, status }: VoiceoverCardProps) {
  const [duration, setDuration] = useState<number | null>(null)

  return (
    <SectionCard title="Voiceover" status={status}>
      {audioUrl ? (
        <div className={styles.body}>
          <p className={styles.description}>AI-generated narration</p>
          <audio
            controls
            src={audioUrl}
            className={styles.player}
            onLoadedMetadata={(event) => setDuration(event.currentTarget.duration)}
          >
            Your browser does not support the audio element.
          </audio>
          {duration !== null || provider ? (
            <dl className={styles.meta}>
              {duration !== null ? (
                <div className={styles.metaItem}>
                  <dt>Duration</dt>
                  <dd>{formatDuration(duration)}</dd>
                </div>
              ) : null}
              {provider ? (
                <div className={styles.metaItem}>
                  <dt>Provider</dt>
                  <dd className={styles.providerValue}>{provider}</dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </div>
      ) : (
        <p className={styles.placeholder}>
          {status === 'failed'
            ? "Voiceover couldn't be generated for this campaign."
            : 'Voiceover audio will appear here once ready.'}
        </p>
      )}
    </SectionCard>
  )
}
