import { STATUS_LABEL, type AssetStatus } from './asset'
import styles from './SectionHeading.module.css'

export interface SectionHeadingProps {
  title: string
  status?: AssetStatus
}

export function SectionHeading({ title, status }: SectionHeadingProps) {
  return (
    <div className={styles.row}>
      <h2 className={styles.title}>{title}</h2>
      {status ? (
        <span className={styles.statusBadge} data-status={status}>
          {STATUS_LABEL[status]}
        </span>
      ) : null}
    </div>
  )
}
