import type { ComponentPropsWithoutRef, CSSProperties } from 'react'

import styles from './FloatingScrap.module.css'

export type ScrapTone = 'indigo' | 'sky' | 'coral' | 'amber' | 'emerald'

interface FloatingScrapProps extends ComponentPropsWithoutRef<'div'> {
  tone: ScrapTone
  rotate?: number
  span?: 1 | 2
}

export function FloatingScrap({
  tone,
  rotate = 0,
  span = 1,
  className,
  children,
  style,
  ...rest
}: FloatingScrapProps) {
  return (
    <div
      {...rest}
      className={[styles.scrap, span === 2 ? styles.wide : '', className].filter(Boolean).join(' ')}
      data-tone={tone}
      style={{ ...style, '--scrap-rotate': `${rotate}deg` } as CSSProperties}
    >
      {children}
    </div>
  )
}
