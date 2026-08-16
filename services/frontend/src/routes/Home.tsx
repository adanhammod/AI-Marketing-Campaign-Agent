import { lazy, Suspense } from 'react'

import { HeroFallback } from '../components/hero/HeroFallback'

const AureleHero = lazy(() =>
  import('../components/hero/AureleHero').then((module) => ({ default: module.AureleHero })),
)

export function Home() {
  return (
    <Suspense fallback={<HeroFallback />}>
      <AureleHero />
    </Suspense>
  )
}
