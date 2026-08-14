import { SectionHeading } from './SectionHeading'
import type { AssetStatus } from './asset'
import styles from './ImageGallery.module.css'

export interface GalleryImage {
  url: string
  sceneNumber: number | null | undefined
  attribution?: string | null
}

export interface ImageGalleryProps {
  images: GalleryImage[]
  status: AssetStatus
}

export function ImageGallery({ images, status }: ImageGalleryProps) {
  return (
    <section className={styles.section} aria-label="Generated visuals">
      <SectionHeading title="Generated Visuals" status={status} />
      {images.length > 0 ? (
        <div className={styles.grid}>
          {images.map((image) => (
            <figure key={image.url} className={styles.figure}>
              <img
                src={image.url}
                alt={`Scene ${image.sceneNumber ?? ''} visual for the campaign`.trim()}
                className={styles.image}
              />
              <figcaption className={styles.caption}>
                {image.sceneNumber ? `Scene ${image.sceneNumber}` : 'Scene'}
                {image.attribution ? ` · ${image.attribution}` : ''}
              </figcaption>
            </figure>
          ))}
        </div>
      ) : (
        <p className={styles.placeholder}>
          {status === 'failed'
            ? "Images couldn't be generated for this campaign."
            : 'Generated images will appear here once ready.'}
        </p>
      )}
    </section>
  )
}
