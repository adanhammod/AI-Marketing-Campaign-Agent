import type { components } from '../../api/schema.gen'
import { SectionCard } from './SectionCard'
import type { AssetStatus } from './asset'
import styles from './StrategyCard.module.css'

type StrategyOutput = components['schemas']['StrategyOutput']

export interface StrategyCardProps {
  strategy: StrategyOutput | null | undefined
  status: AssetStatus
}

export function StrategyCard({ strategy, status }: StrategyCardProps) {
  return (
    <SectionCard title="Marketing Strategy" status={status}>
      {strategy ? (
        <div className={styles.body}>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>Objective</span>
            <p className={styles.fieldValue}>{strategy.objective}</p>
          </div>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>Audience</span>
            <p className={styles.fieldValue}>{strategy.audience}</p>
          </div>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>Key message</span>
            <p className={styles.fieldValue}>{strategy.key_message}</p>
          </div>
          <div className={styles.field}>
            <span className={styles.fieldLabel}>Positioning</span>
            <p className={styles.fieldValue}>{strategy.positioning}</p>
          </div>
          {Object.keys(strategy.channel_rationale).length > 0 ? (
            <div className={styles.field}>
              <span className={styles.fieldLabel}>Channels</span>
              <div className={styles.chips}>
                {Object.keys(strategy.channel_rationale).map((channel) => (
                  <span key={channel} className={styles.chip}>
                    {channel}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : (
        <p className={styles.placeholder}>
          {status === 'failed'
            ? "Strategy couldn't be generated for this campaign."
            : 'Strategy will appear here once generated.'}
        </p>
      )}
    </SectionCard>
  )
}
