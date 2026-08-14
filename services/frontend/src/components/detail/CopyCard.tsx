import { useState } from 'react'

import type { components } from '../../api/schema.gen'
import { SectionCard } from './SectionCard'
import type { AssetStatus } from './asset'
import styles from './CopyCard.module.css'

type CampaignCopy = components['schemas']['CampaignCopy']

export interface CopyCardProps {
  copy: CampaignCopy | null | undefined
  status: AssetStatus
}

export function CopyCard({ copy, status }: CopyCardProps) {
  const [activeIndex, setActiveIndex] = useState(0)
  const variants = copy?.channel_variants ?? []
  const active = variants[Math.min(activeIndex, variants.length - 1)]

  return (
    <SectionCard title="Social Copy" status={status}>
      {active ? (
        <>
          <div role="tablist" aria-label="Platform" className={styles.tabList}>
            {variants.map((variant, index) => (
              <button
                key={variant.channel}
                type="button"
                role="tab"
                id={`copy-tab-${variant.channel}`}
                aria-selected={variant === active}
                aria-controls={`copy-panel-${variant.channel}`}
                className={styles.tab}
                data-active={variant === active}
                onClick={() => setActiveIndex(index)}
              >
                {variant.channel}
              </button>
            ))}
          </div>
          <div
            role="tabpanel"
            id={`copy-panel-${active.channel}`}
            aria-labelledby={`copy-tab-${active.channel}`}
            className={styles.panel}
          >
            <p className={styles.headline}>{active.headline}</p>
            <p className={styles.caption}>{active.caption}</p>
            <p className={styles.cta}>{active.call_to_action}</p>
            {active.hashtags.length > 0 ? (
              <p className={styles.hashtags}>{active.hashtags.join(' ')}</p>
            ) : null}
            <p className={styles.charCount}>{active.caption.length} characters</p>
          </div>
        </>
      ) : (
        <p className={styles.placeholder}>
          {status === 'failed'
            ? "Copy couldn't be generated for this campaign."
            : 'Copy will appear here once generated.'}
        </p>
      )}
    </SectionCard>
  )
}
