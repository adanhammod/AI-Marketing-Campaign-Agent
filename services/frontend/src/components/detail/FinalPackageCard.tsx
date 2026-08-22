import styles from './FinalPackageCard.module.css'

interface FinalPackageCardProps {
  downloadUrl?: string
}

export function FinalPackageCard({ downloadUrl }: FinalPackageCardProps) {
  return (
    <section className={styles.card} aria-label="Campaign package">
      <h2 className={styles.title}>Campaign ready</h2>
      <p className={styles.description}>
        Your campaign has finished generating and is ready to use.
      </p>
      {downloadUrl ? (
        <div className={styles.actions}>
          <a href={downloadUrl} className={styles.downloadButton}>
            Download campaign package
          </a>
        </div>
      ) : null}
    </section>
  )
}
