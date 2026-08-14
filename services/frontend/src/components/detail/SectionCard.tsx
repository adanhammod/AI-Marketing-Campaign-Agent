import type { ReactNode } from 'react'

import { SectionHeading } from './SectionHeading'
import type { AssetStatus } from './asset'
import styles from './SectionCard.module.css'

export interface SectionCardProps {
  title: string
  status?: AssetStatus
  ariaLabel?: string
  children: ReactNode
}

export function SectionCard({ title, status, ariaLabel, children }: SectionCardProps) {
  return (
    <section className={styles.card} aria-label={ariaLabel ?? title}>
      <SectionHeading title={title} status={status} />
      {children}
    </section>
  )
}
