import * as THREE from 'three'

// Mechanical port of design-reference/The Light Table/aurele-hero-3d.html.
// Camera keyframes, timing, easing, geometry, lighting, materials, stage
// timestamps, portrait behavior, no-loop/settle behavior, and the
// window.AURELE_HERO integration surface are transcribed unchanged. The
// only deviations from the approved source are: (1) DOM lookups become
// function parameters so this runs inside a React-owned canvas instead of
// a standalone page, (2) texture URLs point at the app's re-encoded WebP
// copies of the three approved shots instead of the original PNG paths,
// and (3) a dispose() teardown is added for React mount/unmount, which the
// original standalone page never needed.

export interface AureleHeroStage {
  name: string
  t: number
}

export interface AureleHeroStageEvent {
  stage: string
  time: number
  progress: number
}

export interface AureleHeroApi {
  readonly duration: number
  readonly stages: AureleHeroStage[]
  readonly time: number
  readonly progress: number
  readonly stage: string | null
  readonly settled: boolean
  seek(t: number): void
  onStage(callback: (event: AureleHeroStageEvent) => void): () => void
}

export interface AureleHeroCameraDebug {
  pos: number[]
  fov: number
}

export interface AureleHeroProbeResult {
  ndcX: [number, number]
  ndcY: [number, number]
  behind: boolean
  frameW: number
  frameH: number
}

declare global {
  interface Window {
    AURELE_HERO?: AureleHeroApi
    __auSeek?: (t: number) => void
    __auCam?: () => AureleHeroCameraDebug
    __auProbe?: (name: string) => 'missing' | AureleHeroProbeResult
  }
}

export interface AureleHeroHandle {
  dispose(): void
}

// Re-encoded (WebP, identical pixel dimensions, no crop/recolor/resize) copies
// of design-reference/The Light Table/uploads/aurele-shot-*.png, served from public/.
const TEXTURE_DETAIL_URL = '/hero/aurele-shot-01-detail.webp'
const TEXTURE_LIFESTYLE_URL = '/hero/aurele-shot-02-lifestyle.webp'
const TEXTURE_HERO_URL = '/hero/aurele-shot-03-hero.webp'

export function createAureleHero(canvas: HTMLCanvasElement, root: HTMLElement): AureleHeroHandle {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.outputColorSpace = THREE.SRGBColorSpace
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.06
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFShadowMap

  const scene = new THREE.Scene()
  scene.background = new THREE.Color('#EFE6D5')

  const camera = new THREE.PerspectiveCamera(38, 16 / 9, 0.05, 40)

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches

  /* ---------- pointer parallax ----------
     A small, damped camera offset that tracks pointer position — cinematic
     parallax, not free orbit; there is no way for it to rotate the camera
     around the scene. Its contribution is scaled by the same `life` factor
     that already decays handheld drift to zero at the settle (see render()),
     so the authored Review frame is reached with zero residual offset either
     way. Fine-pointer devices only, and never under reduced motion. */
  const pointerFine = !reduced && window.matchMedia('(pointer: fine)').matches
  const PARALLAX_X = 0.05
  const PARALLAX_Y = 0.026
  const PARALLAX_DAMPING = 0.06
  let pointerTargetX = 0,
    pointerTargetY = 0
  let pointerX = 0,
    pointerY = 0
  function onPointerMove(e: PointerEvent) {
    pointerTargetX = (e.clientX / window.innerWidth) * 2 - 1
    pointerTargetY = (e.clientY / window.innerHeight) * 2 - 1
  }
  if (pointerFine) window.addEventListener('pointermove', onPointerMove)

  /* ---------- lighting ---------- */
  const sunLight = new THREE.DirectionalLight('#FFF3DE', 2.6)
  sunLight.position.set(-4.2, 4.0, 2.2)
  sunLight.castShadow = true
  sunLight.shadow.mapSize.set(2048, 2048)
  sunLight.shadow.radius = 4
  sunLight.shadow.bias = -0.0005
  const sc = sunLight.shadow.camera
  sc.left = -3.4
  sc.right = 3.4
  sc.top = 3.4
  sc.bottom = -2.2
  sc.near = 0.5
  sc.far = 16
  scene.add(sunLight)

  const skyFill = new THREE.HemisphereLight('#FFFBF0', '#D9C9AE', 0.55)
  scene.add(skyFill)

  const bounce = new THREE.DirectionalLight('#FFF6E6', 0.35)
  bounce.position.set(3.4, 1.2, 1.8)
  scene.add(bounce)

  /* ---------- materials ---------- */
  const paper = new THREE.MeshStandardMaterial({ name: 'paper', color: '#FFFCF3', roughness: 0.92, metalness: 0 })
  const travertine = new THREE.MeshStandardMaterial({ name: 'travertine', color: '#E4D6BC', roughness: 0.78, metalness: 0 })
  const plaster = new THREE.MeshStandardMaterial({ name: 'plaster', color: '#EFE6D5', roughness: 0.95, metalness: 0 })
  const bezel = new THREE.MeshStandardMaterial({ name: 'bezel', color: '#F2EADB', roughness: 0.55, metalness: 0.15 })
  const indigo = new THREE.MeshStandardMaterial({ name: 'indigo', color: '#3E4880', roughness: 0.45, metalness: 0.1 })

  /* ---------- surfacing ----------
     Real stone and plaster are authored here rather than left as flat color:
     value-noise fields (fBm) drive colour, roughness and bump per surface, so
     the room breaks up under raking light the way a photographed wall does. */
  function makeNoise(seed: number) {
    const P = new Uint8Array(512)
    let s = seed
    const rnd = () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff
    const perm = Array.from({ length: 256 }, (_, i) => i)
    for (let i = 255; i > 0; i--) {
      const j = (rnd() * (i + 1)) | 0
      const t = perm[i]
      perm[i] = perm[j]
      perm[j] = t
    }
    for (let i = 0; i < 512; i++) P[i] = perm[i & 255]
    const fade = (t: number) => t * t * t * (t * (t * 6 - 15) + 10)
    const grad = (h: number, x: number, y: number) => ((h & 1 ? -x : x) + (h & 2 ? -y : y))
    const noise2 = (x: number, y: number) => {
      const X = Math.floor(x) & 255,
        Y = Math.floor(y) & 255
      const xf = x - Math.floor(x),
        yf = y - Math.floor(y)
      const u = fade(xf),
        v = fade(yf)
      const aa = P[P[X] + Y],
        ab = P[P[X] + Y + 1],
        ba = P[P[X + 1] + Y],
        bb = P[P[X + 1] + Y + 1]
      const x1 = grad(aa, xf, yf) * (1 - u) + grad(ba, xf - 1, yf) * u
      const x2 = grad(ab, xf, yf - 1) * (1 - u) + grad(bb, xf - 1, yf - 1) * u
      return (x1 * (1 - v) + x2 * v) * 0.7
    }
    return (x: number, y: number, oct: number, lac: number, gain: number) => {
      let v = 0,
        a = 0.5,
        f = 1,
        norm = 0
      for (let o = 0; o < oct; o++) {
        v += a * noise2(x * f, y * f)
        norm += a
        a *= gain
        f *= lac
      }
      return v / norm
    }
  }

  type Fbm = ReturnType<typeof makeNoise>
  interface SurfaceSample {
    r: number
    g: number
    b: number
    rough: number
    bump: number
  }

  function surface(size: number, seed: number, shade: (u: number, v: number, fbm: Fbm) => SurfaceSample) {
    const col = document.createElement('canvas')
    col.width = col.height = size
    const rgh = document.createElement('canvas')
    rgh.width = rgh.height = size
    const bmp = document.createElement('canvas')
    bmp.width = bmp.height = size
    const cI = col.getContext('2d')!.createImageData(size, size)
    const rI = rgh.getContext('2d')!.createImageData(size, size)
    const bI = bmp.getContext('2d')!.createImageData(size, size)
    const fbm = makeNoise(seed)
    for (let y = 0; y < size; y++) {
      for (let x = 0; x < size; x++) {
        const i = (y * size + x) * 4
        const o = shade(x / size, y / size, fbm)
        cI.data[i] = o.r
        cI.data[i + 1] = o.g
        cI.data[i + 2] = o.b
        cI.data[i + 3] = 255
        rI.data[i] = rI.data[i + 1] = rI.data[i + 2] = o.rough
        rI.data[i + 3] = 255
        bI.data[i] = bI.data[i + 1] = bI.data[i + 2] = o.bump
        bI.data[i + 3] = 255
      }
    }
    col.getContext('2d')!.putImageData(cI, 0, 0)
    rgh.getContext('2d')!.putImageData(rI, 0, 0)
    bmp.getContext('2d')!.putImageData(bI, 0, 0)
    const wrap = (c: HTMLCanvasElement, srgb?: boolean) => {
      const t = new THREE.CanvasTexture(c)
      t.wrapS = t.wrapT = THREE.RepeatWrapping
      if (srgb) t.colorSpace = THREE.SRGBColorSpace
      t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy())
      return t
    }
    return { map: wrap(col, true), roughnessMap: wrap(rgh), bumpMap: wrap(bmp) }
  }

  /* travertine: mottled sedimentary stone — clustered pitting and soft cloudy
     veining rather than linear grain, which is what separates stone from timber */
  const stone = surface(256, 8123, (u, v, fbm) => {
    const cloud = fbm(u * 2.6, v * 2.9, 4, 2.15, 0.56)
    const mottle = fbm(u * 6.5 + 19, v * 7.1 + 5, 3, 2.3, 0.52)
    const vein = Math.pow(Math.abs(fbm(u * 4.1 + 31, v * 3.6 + 13, 2, 2.4, 0.5)), 1.4)
    const grain = fbm(u * 54, v * 58, 2, 2.2, 0.5)
    const poreField = fbm(u * 34, v * 36, 2, 2.5, 0.5)
    const poreSpot = fbm(u * 130, v * 134, 2, 2.3, 0.5)
    const pitted = poreField > 0.12 && poreSpot > 0.22 ? (poreSpot - 0.22) * 3.0 : 0
    let l = 0.93 + cloud * 0.075 + mottle * 0.05 + grain * 0.03 - vein * 0.05 - pitted * 0.34
    l = Math.max(0.5, Math.min(1.02, l))
    return {
      r: 231 * l,
      g: 221 * l * (1 - vein * 0.01),
      b: 202 * l * (1 - cloud * 0.035 - vein * 0.03),
      rough: 172 + grain * 34 + pitted * 76,
      bump: 128 + cloud * 14 + mottle * 18 + grain * 16 - pitted * 104,
    }
  })
  travertine.map = stone.map
  travertine.roughnessMap = stone.roughnessMap
  travertine.bumpMap = stone.bumpMap
  travertine.bumpScale = 0.16
  travertine.color.set('#FFFFFF')
  travertine.roughness = 1
  ;[stone.map, stone.roughnessMap, stone.bumpMap].forEach((t) => t.repeat.set(1.7, 2.1))

  /* plaster: broad trowel mottling, fine tooth, no repeating pattern at this scale */
  const wallSurf = surface(256, 4477, (u, v, fbm) => {
    const trowel = fbm(u * 1.7, v * 1.9, 4, 2.2, 0.58)
    const sweep = fbm(u * 4.5 + 7, v * 3.2 + 19, 2, 2.4, 0.5)
    const tooth = fbm(u * 62, v * 66, 2, 2.3, 0.5)
    let l = 0.975 + trowel * 0.085 + sweep * 0.032 + tooth * 0.024
    l = Math.max(0.82, Math.min(1.06, l))
    return {
      r: 247 * l,
      g: 239 * l * (1 + trowel * 0.008),
      b: 223 * l * (1 - trowel * 0.014),
      rough: 214 + tooth * 30 + trowel * 18,
      bump: 128 + trowel * 40 + tooth * 26,
    }
  })
  plaster.map = wallSurf.map
  plaster.roughnessMap = wallSurf.roughnessMap
  plaster.bumpMap = wallSurf.bumpMap
  plaster.bumpScale = 0.1
  plaster.color.set('#FFFFFF')
  plaster.roughness = 1
  ;[wallSurf.map, wallSurf.roughnessMap, wallSurf.bumpMap].forEach((t) => t.repeat.set(2.2, 1.2))

  /* paper: faint laid tooth, so sheets catch the raking light like real stock */
  const paperSurf = surface(128, 991, (u, v, fbm) => {
    const tooth = fbm(u * 78, v * 82, 2, 2.3, 0.5)
    const l = 0.985 + tooth * 0.02
    return { r: 255 * l, g: 252 * l, b: 244 * l, rough: 232 + tooth * 22, bump: 128 + tooth * 30 }
  })
  paper.map = paperSurf.map
  paper.bumpMap = paperSurf.bumpMap
  paper.bumpScale = 0.05
  paper.color.set('#FFFFFF')
  ;[paperSurf.map, paperSurf.bumpMap].forEach((t) => t.repeat.set(3, 3))

  /* ---------- room ---------- */
  const room = new THREE.Group()
  room.name = 'room'

  const backWall = new THREE.Mesh(new THREE.BoxGeometry(8, 4, 0.15), plaster)
  backWall.name = 'backWall'
  backWall.position.set(0, 2, -3.2)
  backWall.receiveShadow = true
  room.add(backWall)

  const sideWall = new THREE.Mesh(new THREE.BoxGeometry(0.15, 4, 6), plaster)
  sideWall.name = 'sideWall'
  sideWall.position.set(-3.1, 2, -0.4)
  sideWall.receiveShadow = true
  room.add(sideWall)

  const table = new THREE.Mesh(new THREE.BoxGeometry(9.0, 0.16, 6.2), travertine)
  table.name = 'table'
  table.position.set(0.45, -0.08, -1.5)
  table.receiveShadow = true
  table.castShadow = true
  room.add(table)

  /* floor beyond the table, so the space reads as a room rather than a void */
  const floor = new THREE.Mesh(
    new THREE.BoxGeometry(14, 0.1, 14),
    new THREE.MeshStandardMaterial({
      name: 'floor',
      color: '#D8C9AE',
      roughness: 0.9,
      map: stone.map,
      bumpMap: stone.bumpMap,
      bumpScale: 0.1,
    }),
  )
  floor.name = 'floor'
  floor.position.set(0, -0.95, -0.6)
  floor.receiveShadow = true
  room.add(floor)
  scene.add(room)

  /* ---------- photographic textures ---------- */
  const loader = new THREE.TextureLoader()
  function photo(url: string) {
    const t = loader.load(url)
    t.colorSpace = THREE.SRGBColorSpace
    t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy())
    return t
  }
  const texDetail = photo(TEXTURE_DETAIL_URL)
  const texLifestyle = photo(TEXTURE_LIFESTYLE_URL)
  const texHero = photo(TEXTURE_HERO_URL)

  function photoMat(tex: THREE.Texture, name: string) {
    return new THREE.MeshStandardMaterial({ name, map: tex, roughness: 0.62, metalness: 0 })
  }
  /* box face order: +x, -x, +y, -y, +z, -z — photo on the +z face only */
  function printedBox(w: number, h: number, d: number, tex: THREE.Texture, name: string) {
    const faces = [paper, paper, paper, paper, photoMat(tex, name + 'Face'), paper]
    const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), faces)
    m.name = name
    m.castShadow = true
    m.receiveShadow = true
    return m
  }

  /* ---------- campaign ---------- */
  const campaign = new THREE.Group()
  campaign.name = 'campaign'

  const wallPrint = printedBox(1.3, 0.86, 0.012, texHero, 'wallPrint')
  wallPrint.position.set(-2.78, 1.16, -3.1)
  campaign.add(wallPrint)

  const pin = new THREE.Mesh(new THREE.SphereGeometry(0.012, 20, 16), indigo)
  pin.name = 'pin'
  pin.position.set(-2.78, 1.64, -3.08)
  pin.castShadow = true
  campaign.add(pin)

  const display = new THREE.Group()
  display.name = 'display'
  const shell = new THREE.Mesh(new THREE.BoxGeometry(1.1, 0.66, 0.045), bezel)
  shell.name = 'displayShell'
  shell.castShadow = true
  shell.receiveShadow = true
  display.add(shell)
  const screenMat = photoMat(texHero, 'screen')
  screenMat.roughness = 0.34
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(1.04, 0.585), screenMat)
  screen.name = 'screen'
  screen.position.z = 0.024
  display.add(screen)
  const barGeo = new THREE.PlaneGeometry(1.04, 0.001)
  const barMat = new THREE.MeshStandardMaterial({ name: 'letterbox', color: '#232C49', roughness: 0.5 })
  const barTop = new THREE.Mesh(barGeo, barMat)
  barTop.name = 'barTop'
  barTop.position.set(0, 0.2925, 0.0245)
  display.add(barTop)
  const barBot = new THREE.Mesh(barGeo, barMat)
  barBot.name = 'barBottom'
  barBot.position.set(0, -0.2925, 0.0245)
  display.add(barBot)
  const stand = new THREE.Mesh(new THREE.BoxGeometry(0.22, 0.012, 0.14), bezel)
  stand.name = 'displayStand'
  stand.position.set(0, -0.336, 0.02)
  stand.castShadow = true
  display.add(stand)
  display.position.set(1.05, 0.342, -1.55)
  display.rotation.y = THREE.MathUtils.degToRad(-8)
  campaign.add(display)

  const board1 = printedBox(0.3, 0.175, 0.008, texDetail, 'board1')
  board1.position.set(-0.28, 0.09, -0.7)
  board1.rotation.set(THREE.MathUtils.degToRad(-7), THREE.MathUtils.degToRad(5), 0)
  campaign.add(board1)

  const board2 = printedBox(0.3, 0.175, 0.008, texLifestyle, 'board2')
  board2.position.set(0.18, 0.09, -0.59)
  board2.rotation.set(THREE.MathUtils.degToRad(-7), THREE.MathUtils.degToRad(-1), 0)
  campaign.add(board2)

  const board3 = printedBox(0.3, 0.175, 0.008, texHero, 'board3')
  board3.position.set(0.64, 0.09, -0.47)
  board3.rotation.set(THREE.MathUtils.degToRad(-7), THREE.MathUtils.degToRad(-7), 0)
  campaign.add(board3)
  scene.add(campaign)
  const board3Face = board3.material[4] as THREE.MeshStandardMaterial

  /* ---------- printed development material (canvas textures) ---------- */
  function sheetTexture(w: number, h: number, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void) {
    const c = document.createElement('canvas')
    c.width = w
    c.height = h
    const ctx = c.getContext('2d')!
    ctx.fillStyle = '#FFFCF3'
    ctx.fillRect(0, 0, w, h)
    draw(ctx, w, h)
    const t = new THREE.CanvasTexture(c)
    t.colorSpace = THREE.SRGBColorSpace
    t.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy())
    return t
  }
  const bar = (ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, col: string) => {
    ctx.fillStyle = col
    ctx.fillRect(x, y, w, h)
  }

  const briefTex = sheetTexture(720, 1000, (ctx) => {
    bar(ctx, 96, 150, 380, 26, 'rgba(62,72,128,.62)')
    bar(ctx, 96, 232, 470, 16, 'rgba(92,78,56,.3)')
    bar(ctx, 96, 286, 404, 16, 'rgba(92,78,56,.26)')
    bar(ctx, 96, 340, 300, 16, 'rgba(92,78,56,.22)')
    bar(ctx, 96, 420, 220, 3, 'rgba(92,78,56,.28)')
    bar(ctx, 96, 480, 372, 15, 'rgba(92,78,56,.2)')
    bar(ctx, 96, 530, 268, 15, 'rgba(92,78,56,.17)')
    bar(ctx, 96, 620, 330, 15, 'rgba(92,78,56,.15)')
    bar(ctx, 96, 670, 250, 15, 'rgba(92,78,56,.13)')
  })
  const briefSheet = new THREE.Mesh(new THREE.BoxGeometry(0.3, 0.0025, 0.42), [
    paper,
    paper,
    new THREE.MeshStandardMaterial({ name: 'briefFace', map: briefTex, roughness: 0.9 }),
    paper,
    paper,
    paper,
  ])
  briefSheet.name = 'briefSheet'
  briefSheet.position.set(-0.52, 0.00125, 0.86)
  briefSheet.rotation.y = THREE.MathUtils.degToRad(-8)
  briefSheet.castShadow = true
  briefSheet.receiveShadow = true
  scene.add(briefSheet)

  const pencil = new THREE.Mesh(new THREE.CylinderGeometry(0.006, 0.006, 0.2, 20), indigo)
  pencil.name = 'pencil'
  pencil.position.set(-0.4, 0.0085, 0.8)
  pencil.rotation.set(0, THREE.MathUtils.degToRad(-24), THREE.MathUtils.degToRad(90))
  pencil.castShadow = true
  scene.add(pencil)

  const proofTex = sheetTexture(840, 1180, (ctx, w) => {
    ctx.strokeStyle = 'rgba(86,72,50,.6)'
    ctx.lineWidth = 2
    ;[
      [26, 26, 74, 26],
      [26, 26, 26, 74],
      [w - 26, 26, w - 74, 26],
      [w - 26, 26, w - 26, 74],
    ].forEach((l) => {
      ctx.beginPath()
      ctx.moveTo(l[0], l[1])
      ctx.lineTo(l[2], l[3])
      ctx.stroke()
    })
    bar(ctx, 80, 700, 430, 18, 'rgba(92,78,56,.22)')
    bar(ctx, 80, 748, 330, 18, 'rgba(92,78,56,.18)')
    bar(ctx, 80, 796, 386, 18, 'rgba(92,78,56,.15)')
    ctx.strokeStyle = 'rgba(62,72,128,.6)'
    ctx.lineWidth = 4
    ctx.strokeRect(600, 980, 170, 58)
  })
  const proofFaceMat = new THREE.MeshStandardMaterial({ name: 'proofFace', map: proofTex, roughness: 0.9 })
  const proofSheet = new THREE.Mesh(new THREE.BoxGeometry(0.42, 0.003, 0.59), [
    paper,
    paper,
    proofFaceMat,
    paper,
    paper,
    paper,
  ])
  proofSheet.name = 'proofSheet'
  proofSheet.position.set(-0.3, 0.0015, 0.28)
  proofSheet.rotation.y = THREE.MathUtils.degToRad(-5)
  proofSheet.castShadow = true
  proofSheet.receiveShadow = true
  scene.add(proofSheet)

  /* the proof carries the campaign image itself, printed above the copy block */
  const proofImage = new THREE.Mesh(new THREE.PlaneGeometry(0.35, 0.2), photoMat(texHero, 'proofImage'))
  proofImage.name = 'proofImage'
  proofImage.rotation.x = -Math.PI / 2
  proofImage.position.set(0, 0.0018, -0.11)
  proofSheet.add(proofImage)

  const stripFaces = [paper, paper, photoMat(texDetail, 'stripFace'), paper, paper, paper]
  const contactStrip = new THREE.Mesh(new THREE.BoxGeometry(0.19, 0.002, 0.06), stripFaces)
  contactStrip.name = 'contactStrip'
  contactStrip.position.set(0.68, 0.001, 0.8)
  contactStrip.rotation.y = THREE.MathUtils.degToRad(9)
  contactStrip.castShadow = true
  scene.add(contactStrip)

  const voCanvas = document.createElement('canvas')
  voCanvas.width = 680
  voCanvas.height = 180
  const voCtx = voCanvas.getContext('2d')!
  const voTex = new THREE.CanvasTexture(voCanvas)
  voTex.colorSpace = THREE.SRGBColorSpace
  function drawVO(amp: number) {
    voCtx.fillStyle = '#FFFCF3'
    voCtx.fillRect(0, 0, 680, 180)
    const n = 42
    for (let i = 0; i < n; i++) {
      const base = 0.22 + 0.5 * Math.abs(Math.sin(i * 1.7))
      const h = 150 * base * (1 + 0.4 * amp * Math.sin(i * 0.9 + amp * 9))
      voCtx.fillStyle = i < 30 ? 'rgba(62,72,128,.85)' : 'rgba(92,78,56,.3)'
      voCtx.fillRect(24 + i * 15.4, 90 - h / 2, 6, h)
    }
    voTex.needsUpdate = true
  }
  drawVO(0)
  const voStrip = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.003, 0.09), [
    paper,
    paper,
    new THREE.MeshStandardMaterial({ name: 'voFace', map: voTex, roughness: 0.9 }),
    paper,
    paper,
    paper,
  ])
  voStrip.name = 'voStrip'
  voStrip.position.set(0.86, 0.0015, -1.05)
  voStrip.rotation.y = THREE.MathUtils.degToRad(-6)
  voStrip.castShadow = true
  scene.add(voStrip)

  /* ---------- camera path ---------- */
  const KEYS = [
    { t: 0.0, p: [-0.88, 0.44, 1.34], l: [-0.6, 0.02, 0.72] },
    { t: 1.2, p: [-0.72, 0.46, 1.06], l: [-0.48, 0.04, 0.46] },
    { t: 2.5, p: [-0.2, 0.52, 0.62], l: [-0.1, 0.05, -0.2] },
    { t: 4.2, p: [0.02, 0.46, 0.52], l: [0.18, 0.11, -0.59] },
    { t: 5.8, p: [0.66, 0.38, 0.08], l: [0.64, 0.09, -0.48] },
    { t: 6.8, p: [0.88, 0.46, -0.26], l: [0.9, 0.05, -1.0] },
    { t: 8.2, p: [0.34, 0.74, 0.34], l: [0.68, 0.33, -1.53] },
    { t: 9.5, p: [0.16, 0.88, 0.8], l: [0.64, 0.3, -1.5] },
  ]
  const posCurve = new THREE.CatmullRomCurve3(
    KEYS.map((k) => new THREE.Vector3().fromArray(k.p)),
    false,
    'catmullrom',
    0.0,
  )
  const lookCurve = new THREE.CatmullRomCurve3(
    KEYS.map((k) => new THREE.Vector3().fromArray(k.l)),
    false,
    'catmullrom',
    0.0,
  )
  const DUR = KEYS[KEYS.length - 1].t
  const easeInOut = (x: number) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2)

  function uAt(t: number) {
    const n = KEYS.length - 1
    for (let i = 1; i <= n; i++) {
      if (t <= KEYS[i].t) {
        const a = KEYS[i - 1].t,
          b = KEYS[i].t
        return (i - 1 + easeInOut((t - a) / (b - a))) / n
      }
    }
    return 1
  }

  /* ---------- stage model, for the React layer to sync live HTML against ----------
     The 3D world carries no labels or copy: headline, stage chips and the
     Approve / Revise controls all live in the DOM and read their timing here. */
  const STAGES: AureleHeroStage[] = [
    { name: 'BRIEF', t: 0.0 },
    { name: 'STRATEGY', t: 1.2 },
    { name: 'COPY', t: 2.0 },
    { name: 'STORYBOARD', t: 2.5 },
    { name: 'IMAGES', t: 4.2 },
    { name: 'VOICEOVER', t: 5.8 },
    { name: 'VIDEO', t: 6.8 },
    { name: 'REVIEW', t: 8.2 },
  ]
  let lastStage: string | null = null
  let completed = false
  const stageAt = (t: number) => {
    let s = STAGES[0]
    for (const k of STAGES) if (t >= k.t) s = k
    return s.name
  }
  const listeners: ((event: AureleHeroStageEvent) => void)[] = []

  const _p = new THREE.Vector3(),
    _l = new THREE.Vector3()
  let nowT = 0
  function render(t: number) {
    t = Math.max(0, Math.min(t, DUR))
    const u = uAt(t)
    posCurve.getPoint(u, _p)
    lookCurve.getPoint(u, _l)

    /* damped handheld drift, gone by the settle */
    const life = 1 - Math.min(1, Math.max(0, (t - 8.2) / 1.3))
    camera.position.set(_p.x + Math.sin(t * 1.7) * 0.004 * life, _p.y + Math.sin(t * 2.3 + 1.1) * 0.003 * life, _p.z)
    /* portrait: hold the same journey but stand further back, so the boards and
       the finished film still fill a 9:16 frame rather than being cropped */
    if (portraitMode) {
      camera.position.sub(_l).multiplyScalar(1.3).add(_l)
      camera.position.y += 0.06
    }
    if (pointerFine) {
      pointerX += (pointerTargetX - pointerX) * PARALLAX_DAMPING
      pointerY += (pointerTargetY - pointerY) * PARALLAX_DAMPING
      const offsetX = pointerX * PARALLAX_X * life
      const offsetY = -pointerY * PARALLAX_Y * life
      camera.position.x += offsetX
      camera.position.y += offsetY
      _l.x += offsetX * 0.35
      _l.y += offsetY * 0.35
    }
    camera.lookAt(_l)
    camera.rotation.z += THREE.MathUtils.degToRad(0.4) * Math.sin(t * 0.8) * life

    /* board 3 finishes as we arrive on it */
    const grade = Math.min(1, Math.max(0, (t - 3.4) / 1.6))
    board3Face.color.setScalar(0.86 + 0.14 * grade)

    /* voiceover, passed in the world */
    const vo = t > 5.6 && t < 7.2 ? 1 : 0
    if (vo) drawVO((t - 5.6) * 3)

    /* the still resolves into a shot */
    const lb = Math.min(1, Math.max(0, (t - 6.9) / 1.2)) * 0.075
    barTop.scale.y = Math.max(0.001, lb / 0.001)
    barBot.scale.y = Math.max(0.001, lb / 0.001)
    barTop.position.y = 0.2925 - lb / 2
    barBot.position.y = -0.2925 + lb / 2

    renderer.render(scene, camera)

    nowT = t
    const stage = stageAt(t)
    if (stage !== lastStage) {
      lastStage = stage
      root.dataset.stage = stage
      const ev = { stage, time: t, progress: t / DUR }
      listeners.forEach((cb) => cb(ev))
      root.dispatchEvent(new CustomEvent('aurele-stage', { detail: ev, bubbles: true }))
    }
    if (!completed && t >= DUR) {
      completed = true
      root.dispatchEvent(new CustomEvent('aurele-complete', { detail: { stage: 'REVIEW' }, bubbles: true }))
    }
  }

  let portraitMode = false
  function resize() {
    const w = window.innerWidth,
      h = window.innerHeight
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    portraitMode = w / h < 1
    camera.fov = portraitMode ? 46 : 38
    camera.updateProjectionMatrix()
    render(nowT)
  }
  window.addEventListener('resize', resize)
  resize()

  let start: number | null = null,
    raf = 0,
    held = false
  function loop(now: number) {
    if (held) return
    if (start === null) start = now
    const t = (now - start) / 1000
    render(t)
    root.dataset.screenLabel = 'AURELE hero ' + t.toFixed(1) + 's'
    if (t < DUR) raf = requestAnimationFrame(loop)
  }

  if (reduced) {
    held = true
    render(DUR)
  } else {
    raf = requestAnimationFrame(loop)
  }

  /* host timeline + video export: seek is pause-and-hold */
  const seekListener = (e: Event) => {
    held = true
    cancelAnimationFrame(raf)
    const d = (e as CustomEvent<{ time?: number; frame?: number }>).detail || {}
    render(typeof d.time === 'number' ? d.time : (d.frame || 0) / 30)
  }
  root.addEventListener('data-om-seek-to-time-frame', seekListener)

  const auSeek = (t: number) => {
    held = true
    cancelAnimationFrame(raf)
    render(t)
  }
  window.__auSeek = auSeek

  /* integration surface for the React frontend (Claude Code handoff) */
  const aureleHero: AureleHeroApi = {
    duration: DUR,
    stages: STAGES.map((s) => ({ ...s })),
    get time() {
      return nowT
    },
    get progress() {
      return nowT / DUR
    },
    get stage() {
      return lastStage
    },
    get settled() {
      return completed
    },
    seek(t) {
      window.__auSeek?.(t)
    },
    onStage(cb) {
      listeners.push(cb)
      if (lastStage) cb({ stage: lastStage, time: nowT, progress: nowT / DUR })
      return () => listeners.splice(listeners.indexOf(cb), 1)
    },
  }
  window.AURELE_HERO = aureleHero

  const auCam = () => ({ pos: camera.position.toArray().map((v) => +v.toFixed(3)), fov: camera.fov })
  window.__auCam = auCam

  const auProbe = (name: string): 'missing' | AureleHeroProbeResult => {
    const o = scene.getObjectByName(name)
    if (!o) return 'missing'
    const box = new THREE.Box3().setFromObject(o)
    const pts = []
    for (let i = 0; i < 8; i++) {
      const v = new THREE.Vector3(
        i & 1 ? box.max.x : box.min.x,
        i & 2 ? box.max.y : box.min.y,
        i & 4 ? box.max.z : box.min.z,
      ).project(camera)
      pts.push(v)
    }
    const xs = pts.map((p) => p.x),
      ys = pts.map((p) => p.y),
      zs = pts.map((p) => p.z)
    return {
      ndcX: [+Math.min(...xs).toFixed(2), +Math.max(...xs).toFixed(2)],
      ndcY: [+Math.min(...ys).toFixed(2), +Math.max(...ys).toFixed(2)],
      behind: Math.min(...zs) < -1,
      frameW: +((Math.max(...xs) - Math.min(...xs)) / 2).toFixed(2),
      frameH: +((Math.max(...ys) - Math.min(...ys)) / 2).toFixed(2),
    }
  }
  window.__auProbe = auProbe

  return {
    dispose() {
      held = true
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', resize)
      if (pointerFine) window.removeEventListener('pointermove', onPointerMove)
      root.removeEventListener('data-om-seek-to-time-frame', seekListener)

      scene.traverse((object) => {
        const mesh = object as THREE.Mesh
        if (!mesh.isMesh) return
        mesh.geometry.dispose()
        const material = mesh.material
        if (Array.isArray(material)) material.forEach(disposeMaterial)
        else disposeMaterial(material)
      })
      renderer.dispose()

      if (window.AURELE_HERO === aureleHero) delete window.AURELE_HERO
      if (window.__auSeek === auSeek) delete window.__auSeek
      if (window.__auCam === auCam) delete window.__auCam
      if (window.__auProbe === auProbe) delete window.__auProbe
    },
  }
}

function disposeMaterial(material: THREE.Material) {
  material.dispose()
  const withMaps = material as THREE.MeshStandardMaterial
  withMaps.map?.dispose()
  withMaps.roughnessMap?.dispose()
  withMaps.bumpMap?.dispose()
}
