export type AssetStatus = 'pending' | 'generating' | 'ready' | 'failed'

export const STATUS_LABEL: Record<AssetStatus, string> = {
  pending: 'Not started',
  generating: 'Generating…',
  ready: 'Ready',
  failed: "Couldn't generate",
}
