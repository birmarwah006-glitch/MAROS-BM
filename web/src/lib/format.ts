/** Human duration from the manifest's `duration_sec`. */
export function formatDuration(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return ''
  const mins = Math.round(seconds / 60)
  if (mins < 1) return '<1 min'
  if (mins < 60) return `${mins} min`
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return m ? `${h}h ${m}m` : `${h}h`
}

/**
 * A rough difficulty read.
 *
 * IMPORTANT: the backend exposes no difficulty field — there is no concept
 * catalogue at all (GAP 1). Rather than invent a rating, this reports the
 * only honest proxy available: how much lecture time the concept took. The
 * label says "depth", not "difficulty", so it does not claim to be something
 * it is not.
 */
export function depthLabel(seconds: number | undefined): string | null {
  if (!seconds || seconds <= 0) return null
  const mins = seconds / 60
  if (mins < 6) return 'Quick'
  if (mins < 15) return 'Standard'
  return 'In depth'
}
