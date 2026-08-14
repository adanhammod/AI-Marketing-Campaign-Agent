import styles from './ReviewActionsCard.module.css'

export function ReviewActionsCard() {
  return (
    <section className={styles.card} aria-label="Review actions">
      <h2 className={styles.title}>Ready for review</h2>
      <p className={styles.description}>
        Review the generated assets above before approving the campaign.
      </p>
      <div className={styles.actions}>
        <button type="button" className={styles.secondaryButton} disabled title="Coming soon">
          Request changes
        </button>
        <button type="button" className={styles.primaryButton} disabled title="Coming soon">
          Approve campaign
        </button>
      </div>
      <p className={styles.note}>Approving and requesting changes are coming soon.</p>
    </section>
  )
}
