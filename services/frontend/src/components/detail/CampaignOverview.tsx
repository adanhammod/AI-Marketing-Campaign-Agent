import type { components } from '../../api/schema.gen'
import styles from './CampaignOverview.module.css'

type NormalizedCampaignBrief = components['schemas']['NormalizedCampaignBrief']

export interface CampaignOverviewProps {
  brief: NormalizedCampaignBrief
}

export function CampaignOverview({ brief }: CampaignOverviewProps) {
  const cells: { label: string; value: string }[] = [
    { label: 'Campaign goal', value: brief.campaign_goal },
    { label: 'Audience', value: brief.target_audience || 'Not specified' },
    { label: 'Tone', value: brief.tone },
    { label: 'Call to action', value: brief.call_to_action || 'Not specified' },
  ]

  return (
    <section className={styles.grid} aria-label="Campaign overview">
      {cells.map((cell) => (
        <div key={cell.label} className={styles.cell}>
          <span className={styles.label}>{cell.label}</span>
          <p className={styles.value}>{cell.value}</p>
        </div>
      ))}
    </section>
  )
}
