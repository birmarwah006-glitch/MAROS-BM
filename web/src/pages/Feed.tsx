/**
 * The MAROS Meal feed.
 *
 * Full-screen, vertical, swipeable, audio-first — the interaction pattern of
 * short-form video, without borrowing its visual language or its vocabulary.
 * This is MEALS, not reels.
 *
 * Two things keep it a learning surface rather than an entertainment one:
 * the only actions offered are learning actions (practise, replay, what's
 * next), and the practice prompt is attached to the Meal rather than buried
 * a tap away.
 *
 * Audio: browsers refuse autoplay with sound, so a Meal starts muted with an
 * explicit unmute affordance. Audio is core to a Meal, so the prompt is
 * prominent rather than a small icon in a corner.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMeals } from '@/api/hooks'
import { mediaUrl } from '@/api/client'
import type { MealSummary } from '@/api/types'
import { Badge, ButtonLink, EmptyState, ErrorState, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

function formatSeconds(s: number | null) {
  if (!s) return ''
  return `${Math.round(s)}s`
}

function MealCard({
  meal,
  active,
  muted,
  onUnmute,
}: {
  meal: MealSummary
  active: boolean
  muted: boolean
  onUnmute: () => void
}) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const [progress, setProgress] = useState(0)
  const [paused, setPaused] = useState(false)
  const [failed, setFailed] = useState(false)
  const [showPractice, setShowPractice] = useState(false)

  // Only the Meal in view plays. Everything else is paused and rewound so a
  // Meal always starts from its hook.
  useEffect(() => {
    const video = videoRef.current
    if (!video) return
    if (active) {
      video.currentTime = 0
      void video.play().then(() => setPaused(false)).catch(() => setPaused(true))
    } else {
      video.pause()
      video.currentTime = 0
      setShowPractice(false)
    }
  }, [active])

  useEffect(() => {
    const video = videoRef.current
    if (video) video.muted = muted
  }, [muted])

  const toggle = useCallback(() => {
    const video = videoRef.current
    if (!video) return
    if (video.paused) void video.play().then(() => setPaused(false))
    else {
      video.pause()
      setPaused(true)
    }
  }, [])

  return (
    <section
      className="relative h-[100dvh] w-full snap-start snap-always overflow-hidden bg-black"
      aria-label={meal.title}
    >
      {failed ? (
        <div className="flex h-full items-center justify-center px-8">
          <ErrorState
            title="This Meal could not be loaded"
            detail="The rendered video is missing on the server."
          />
        </div>
      ) : (
        <video
          ref={videoRef}
          src={mediaUrl.mealVideo(meal.id)}
          className="absolute inset-0 h-full w-full object-contain"
          playsInline
          loop
          muted={muted}
          preload={active ? 'auto' : 'metadata'}
          onError={() => setFailed(true)}
          onClick={toggle}
          onTimeUpdate={(e) => {
            const v = e.currentTarget
            if (v.duration) setProgress((v.currentTime / v.duration) * 100)
          }}
        />
      )}

      {/* Progress. A thin rule, not a scrubber — this is a lesson, not a clip. */}
      <div className="pointer-events-none absolute inset-x-0 top-0 z-20 h-0.5 bg-white/10">
        <div
          className="h-full bg-green transition-[width] duration-150 ease-linear"
          style={{ width: `${progress}%` }}
        />
      </div>

      {/* Paused affordance */}
      {paused && !failed && (
        <button
          type="button"
          onClick={toggle}
          className="absolute inset-0 z-20 flex items-center justify-center"
          aria-label="Play"
        >
          <span className="flex h-16 w-16 items-center justify-center rounded-full border border-white/25 bg-black/50 backdrop-blur-sm">
            <span className="ml-1 block h-0 w-0 border-y-[11px] border-l-[18px] border-y-transparent border-l-white" />
          </span>
        </button>
      )}

      {/* Sound is core to a Meal, so this is a real prompt, not a corner icon. */}
      {muted && active && !failed && (
        <button
          type="button"
          onClick={onUnmute}
          className="absolute left-1/2 top-6 z-30 -translate-x-1/2 rounded-full border border-green-line bg-black/70 px-4 py-2 font-mono text-micro uppercase tracking-[0.14em] text-green backdrop-blur-sm"
        >
          Tap for sound
        </button>
      )}

      {/* Meal metadata and the learning actions. */}
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-20 bg-gradient-to-t from-black via-black/85 to-transparent px-5 pb-7 pt-16 sm:px-8">
        <div className="pointer-events-auto mx-auto w-full max-w-lg">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="green">Meal</Badge>
            {meal.difficulty && <Badge tone="neutral">{meal.difficulty}</Badge>}
            {meal.duration_sec && (
              <span className="font-mono text-micro text-white/50">
                {formatSeconds(meal.duration_sec)}
              </span>
            )}
          </div>

          <h2 className="mt-2.5 font-display text-lg font-bold leading-tight text-white">
            {meal.title}
          </h2>

          {meal.practice && (
            <div className="mt-3">
              <button
                type="button"
                onClick={() => setShowPractice((v) => !v)}
                aria-expanded={showPractice}
                className="flex w-full items-center gap-3 rounded border border-green-line bg-green-soft px-3.5 py-2.5 text-left transition-colors hover:bg-green-soft/60"
              >
                <span className="font-mono text-micro uppercase tracking-[0.14em] text-green">
                  Your turn
                </span>
                <span className="flex-1 truncate text-sm text-white/80">
                  {showPractice ? 'Hide' : meal.practice.prompt}
                </span>
              </button>

              {/* Expanded only on demand, so the Meal's own captions stay
                  visible while it plays. */}
              {showPractice && (
                <div className="mt-2 rounded border border-white/10 bg-black/70 px-3.5 py-3 backdrop-blur-sm">
                  <p className="text-sm leading-relaxed text-white">
                    {meal.practice.prompt}
                  </p>
                  {meal.practice.hint && (
                    <p className="mt-2 text-xs leading-relaxed text-white/50">
                      {meal.practice.hint}
                    </p>
                  )}
                  <p className="mt-3 font-mono text-micro text-white/40">
                    {meal.objective}
                  </p>
                  {meal.next_concepts.length > 0 && (
                    <p className="mt-2 font-mono text-micro text-white/30">
                      NEXT: {meal.next_concepts.join(' · ')}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

export default function Feed() {
  const { data, isPending, isError, error, refetch } = useMeals()
  const meals = useMemo(() => data?.meals ?? [], [data])

  const containerRef = useRef<HTMLDivElement>(null)
  const [activeIndex, setActiveIndex] = useState(0)
  const [muted, setMuted] = useState(true)

  // Which Meal is in view. Scroll-snap does the movement; this only observes.
  useEffect(() => {
    const root = containerRef.current
    if (!root || meals.length === 0) return

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting && entry.intersectionRatio > 0.6) {
            const i = Number((entry.target as HTMLElement).dataset.index)
            if (!Number.isNaN(i)) setActiveIndex(i)
          }
        }
      },
      { root, threshold: [0.6] },
    )

    root.querySelectorAll('[data-index]').forEach((el) => observer.observe(el))
    return () => observer.disconnect()
  }, [meals.length])

  // Keyboard navigation, because the learning surface is used on desktop too.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const root = containerRef.current
      if (!root) return
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const next = activeIndex + (e.key === 'ArrowDown' ? 1 : -1)
        const target = root.querySelector(`[data-index="${next}"]`)
        target?.scrollIntoView({ behavior: 'smooth' })
      }
      if (e.key === 'm') setMuted((m) => !m)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [activeIndex])

  if (isPending) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-black">
        <Spinner />
      </div>
    )
  }

  if (isError) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-black px-6">
        <ErrorState
          title="Could not load Meals"
          detail={error instanceof Error ? error.message : undefined}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }

  if (meals.length === 0) {
    return (
      <div className="flex h-[100dvh] items-center justify-center bg-black px-6">
        <EmptyState
          title="No Meals have been rendered yet"
          body={
            <>
              A Meal has to be narrated and rendered before it can appear here.
              Run <code className="font-mono text-green">meals/narrate.py</code>{' '}
              then <code className="font-mono text-green">meal-renderer/render.mjs</code>.
            </>
          }
          action={
            <ButtonLink to="/explore" variant="secondary">
              Browse lectures instead
            </ButtonLink>
          }
        />
      </div>
    )
  }

  return (
    <div className="relative bg-black">
      {/* Minimal chrome — the feed is the product, not the navigation. */}
      <div className="pointer-events-none fixed inset-x-0 top-0 z-40 flex items-center justify-between px-5 py-4 sm:px-8">
        <Link
          to="/"
          className="pointer-events-auto flex items-center gap-2"
          aria-label="MAROS home"
        >
          <span className="h-2.5 w-2.5 rounded-[3px] bg-green" aria-hidden />
          <span className="font-display text-sm font-extrabold tracking-[-0.02em] text-white">
            MAROS
          </span>
        </Link>
        <span className="pointer-events-auto font-mono text-micro text-white/40">
          {activeIndex + 1} / {meals.length}
        </span>
      </div>

      <div
        ref={containerRef}
        className={cn(
          'h-[100dvh] snap-y snap-mandatory overflow-y-scroll',
          '[scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
        )}
      >
        {meals.map((meal, i) => (
          <div key={meal.id} data-index={i}>
            <MealCard
              meal={meal}
              active={i === activeIndex}
              muted={muted}
              onUnmute={() => setMuted(false)}
            />
          </div>
        ))}
      </div>
    </div>
  )
}
