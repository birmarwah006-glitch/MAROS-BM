/**
 * Explore — public concept discovery.
 *
 * LECTURE-FIRST (decision D2). The backend has no concept catalogue: there is
 * no GET /concepts, and the only concept registry is the 12 hardcoded
 * Operating-Systems ids in prep_mode.CONCEPTS. See GAP 1 and GAP 2 in
 * docs/frontend-api-gaps.md.
 *
 * So this page browses what genuinely exists — processed lectures and the
 * concepts extracted from them, via GET /lectures — and says plainly that
 * subject filtering is not available yet rather than faking a taxonomy.
 */

import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useLectures, useModules, useReels } from '@/api/hooks'
import { mediaUrl } from '@/api/client'
import type { LectureSummary, Module } from '@/api/types'
import { depthLabel, formatDuration } from '@/lib/format'
import {
  Badge, ButtonLink, Container, EmptyState, ErrorState, Skeleton,
} from '@/components/ui'
import { ReelPlayer } from '@/components/ReelPlayer'
import { cn } from '@/lib/cn'

/* ── Reel rail ─────────────────────────────────────────────────────────── */

/**
 * Reels can only be listed per job (GAP 12), so the rail asks for the reels
 * of one lecture at a time. At today's scale — one lecture with a manifest —
 * that is a single request.
 */
function ReelRail({ lecture }: { lecture: LectureSummary }) {
  const { data, isPending } = useReels(lecture.job_id)
  const ready = data?.reels.filter((r) => r.reel_status === 'done') ?? []

  if (isPending) {
    return (
      <div className="flex gap-4 overflow-x-auto pb-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="aspect-[9/16] w-44 shrink-0" />
        ))}
      </div>
    )
  }

  if (!ready.length) return null

  return (
    <div className="mt-10">
      <div className="mb-4 flex items-baseline gap-3">
        <p className="eyebrow">Concept Meals</p>
        <span className="font-mono text-micro text-muted">
          {ready.length} available
        </span>
      </div>

      {/* Horizontal rail: the discovery mechanic of a short-form feed, without
          borrowing its visual language. */}
      <div className="-mx-5 flex snap-x snap-mandatory gap-4 overflow-x-auto px-5 pb-3 md:mx-0 md:px-0">
        {ready.map((reel) => (
          <div key={reel.module_id} className="w-44 shrink-0 snap-start sm:w-52">
            <ReelPlayer
              src={mediaUrl.reelVideo(lecture.job_id, reel.module_id)}
              concept={reel.plan?.concept ?? reel.module_concept ?? 'Concept'}
            />
            <Link
              to={`/lecture/${lecture.job_id}/module/${reel.module_id}`}
              className="mt-2 block text-xs text-muted2 transition-colors hover:text-green"
            >
              Learn this concept →
            </Link>
          </div>
        ))}
      </div>
    </div>
  )
}

/* ── Concept card ──────────────────────────────────────────────────────── */

function ConceptCard({
  lecture, moduleId, concept, detail,
}: {
  lecture: LectureSummary
  moduleId: number
  concept: string
  /** From GET /modules — carries the real timings. Absent while loading. */
  detail?: Module
}) {
  const duration = formatDuration(detail?.duration_sec)
  const depth = depthLabel(detail?.duration_sec)

  return (
    <Link
      to={`/lecture/${lecture.job_id}/module/${moduleId}`}
      className="card-interactive group flex flex-col p-5"
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-micro text-muted">
          {String(moduleId).padStart(2, '0')}
        </span>
        {depth && <Badge tone="neutral">{depth}</Badge>}
      </div>

      <h3 className="mt-3 flex-1 font-display text-base font-semibold leading-snug text-ink transition-colors group-hover:text-green">
        {concept}
      </h3>

      <div className="mt-5 flex items-center justify-between border-t border-line pt-3">
        <span className="font-mono text-micro text-muted">
          {detail ? `${detail.start}-${detail.end}` : ''}
          {duration && ` · ${duration}`}
        </span>
        <span className="ml-3 shrink-0 font-mono text-micro text-green opacity-0 transition-opacity group-hover:opacity-100">
          LEARN →
        </span>
      </div>
    </Link>
  )
}

/** One lecture and its concepts. Fetches module detail so cards can show real
 *  timings instead of repeating the lecture title six times. */
function LectureSection({ lecture }: { lecture: LectureSummary }) {
  const { data: modules } = useModules(lecture.job_id)
  const byId = new Map((modules ?? []).map((m) => [m.module_id, m]))

  return (
    <section className="mb-14 last:mb-0">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-2">
        <div className="min-w-0">
          <h2 className="font-display text-lg font-bold text-ink">
            {lecture.title}
          </h2>
          <p className="mt-1 flex flex-wrap items-center gap-2 font-mono text-micro text-muted">
            <span>{lecture.total_modules} CONCEPTS</span>
            {lecture.source === 'youtube' && <span>· FROM YOUTUBE</span>}
            {lecture.generated_at && (
              <span>· {new Date(lecture.generated_at).toLocaleDateString()}</span>
            )}
          </p>
        </div>
        <Link
          to={`/lecture/${lecture.job_id}`}
          className="shrink-0 text-xs text-muted2 transition-colors hover:text-green"
        >
          Learning path →
        </Link>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {lecture.modules.map((m) => (
          <ConceptCard
            key={m.module_id}
            lecture={lecture}
            moduleId={m.module_id}
            concept={m.concept}
            detail={byId.get(m.module_id)}
          />
        ))}
      </div>

      <ReelRail lecture={lecture} />
    </section>
  )
}

/* ── Page ──────────────────────────────────────────────────────────────── */

export default function Explore() {
  const { data: lectures, isPending, isError, error, refetch } = useLectures()
  const [selected, setSelected] = useState<string | 'all'>('all')

  const visible = useMemo(
    () =>
      selected === 'all'
        ? (lectures ?? [])
        : (lectures ?? []).filter((l) => l.job_id === selected),
    [lectures, selected],
  )

  const conceptCount = useMemo(
    () => (lectures ?? []).reduce((n, l) => n + l.modules.length, 0),
    [lectures],
  )

  return (
    <Container>
      <div className="py-10 md:py-14">
        <header>
          <p className="eyebrow">Explore</p>
          <h1 className="mt-3 max-w-3xl font-display text-[1.75rem] font-extrabold tracking-[-0.03em] text-ink sm:text-3xl">
            Concepts, extracted from real lectures
          </h1>
          <p className="mt-3 max-w-2xl text-base text-muted2">
            Every concept here came out of a lecture MAROS processed. Open one
            to get its notes, concept map, the clip it came from, and a quiz.
          </p>
        </header>

        {/* Lecture filter. This is deliberately NOT a subject filter: the
            backend has no subject taxonomy (GAP 2), so offering Python /
            C++ / Algorithms tabs would be inventing a catalogue. */}
        {!isPending && !isError && (lectures?.length ?? 0) > 0 && (
          <div className="mt-8 flex flex-wrap items-center gap-2">
            <FilterChip
              active={selected === 'all'}
              onClick={() => setSelected('all')}
            >
              All · {conceptCount}
            </FilterChip>
            {lectures!.map((l) => (
              <FilterChip
                key={l.job_id}
                active={selected === l.job_id}
                onClick={() => setSelected(l.job_id)}
              >
                {l.title}
              </FilterChip>
            ))}
          </div>
        )}

        <div className="mt-10">
          {isPending && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-40" />
              ))}
            </div>
          )}

          {isError && (
            <ErrorState
              title="Could not load lectures"
              detail={error instanceof Error ? error.message : undefined}
              onRetry={() => void refetch()}
            />
          )}

          {!isPending && !isError && (lectures?.length ?? 0) === 0 && (
            <EmptyState
              title="Nothing has been processed yet"
              body={
                <>
                  MAROS builds concepts out of lectures you give it. Add a
                  recording or a YouTube link and it will extract the concepts
                  automatically.
                </>
              }
              action={
                <ButtonLink to="/add" variant="primary">
                  Add the first lecture
                </ButtonLink>
              }
            />
          )}

          {visible.map((lecture) => (
            <LectureSection key={lecture.job_id} lecture={lecture} />
          ))}
        </div>
      </div>
    </Container>
  )
}

function FilterChip({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'max-w-[16rem] truncate rounded border px-3 py-1.5 text-xs transition-colors',
        active
          ? 'border-green-line bg-green-soft text-green'
          : 'border-line2 text-muted2 hover:text-ink',
      )}
    >
      {children}
    </button>
  )
}
