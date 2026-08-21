/**
 * 9:16 Meal player for the Explore rail.
 *
 * TERMINOLOGY: the file and component keep the name `Reel` because they are
 * bound to the backend's `/reels/*` routes and `reel_status` field, which are
 * the source of truth and are not renamed. Only what a learner READS says
 * "Meal".
 *
 * There is no poster/thumbnail endpoint (documented as GAP 10 —
 * `thumbnail_url` is hardcoded null server-side), so instead of inventing an
 * image the card shows the concept over the brand surface until playback
 * starts. `preload="metadata"` keeps a feed of these cheap.
 *
 * Autoplay only ever happens muted, and only when the card is actually on
 * screen — an IntersectionObserver drives it, so scrolling the feed behaves
 * the way a short-form feed should without a library.
 */

import { useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/cn'

export function ReelPlayer({
  src,
  concept,
  className,
  active = true,
}: {
  src: string
  concept: string
  className?: string
  /** False when another card owns playback (e.g. a horizontal rail). */
  active?: boolean
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [started, setStarted] = useState(false)
  const [failed, setFailed] = useState(false)
  const [muted, setMuted] = useState(true)

  useEffect(() => {
    const video = videoRef.current
    if (!video || failed) return

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!active) {
          video.pause()
          return
        }
        if (entry.isIntersecting && entry.intersectionRatio > 0.6) {
          void video.play().then(() => setStarted(true)).catch(() => {
            /* autoplay refused — the poster state stays, tap to play works */
          })
        } else {
          video.pause()
        }
      },
      { threshold: [0, 0.6, 1] },
    )

    observer.observe(video)
    return () => observer.disconnect()
  }, [active, failed])

  if (failed) {
    return (
      <div
        className={cn(
          'flex aspect-[9/16] items-center justify-center rounded border border-line bg-surface2 p-6 text-center',
          className,
        )}
      >
        <p className="text-xs text-muted2">This Meal could not be loaded.</p>
      </div>
    )
  }

  return (
    <div className={cn('group relative overflow-hidden rounded bg-surface2', className)}>
      <video
        ref={videoRef}
        src={src}
        className="aspect-[9/16] h-full w-full object-cover"
        muted={muted}
        loop
        playsInline
        preload="metadata"
        onError={() => setFailed(true)}
        onClick={() => {
          const v = videoRef.current
          if (!v) return
          if (v.paused) void v.play()
          else v.pause()
        }}
      />

      {/* Poster substitute: real information, not a fake image. */}
      {!started && (
        <div className="pointer-events-none absolute inset-0 flex flex-col justify-end bg-gradient-to-t from-black/85 via-black/20 to-transparent p-4">
          <p className="font-display text-sm font-semibold leading-snug text-ink">
            {concept}
          </p>
        </div>
      )}

      <button
        type="button"
        onClick={() => {
          setMuted((m) => !m)
          if (videoRef.current) videoRef.current.muted = !muted
        }}
        className="absolute right-3 top-3 rounded border border-line2 bg-black/60 px-2 py-1 font-mono text-micro text-ink opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
      >
        {muted ? 'UNMUTE' : 'MUTE'}
      </button>
    </div>
  )
}
