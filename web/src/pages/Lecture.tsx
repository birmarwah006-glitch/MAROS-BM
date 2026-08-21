/**
 * Learning path for one lecture.
 *
 * Deliberately not an LMS list — the concepts are drawn as a vertical path.
 *
 * HONEST LIMIT (GAP 5): the backend returns a flat, time-ordered Module[]
 * with no prerequisite edges and no per-concept completion state. So the path
 * shows real lecture ORDER, which is genuine information, and does not
 * pretend to show prerequisites or locks. Nothing here is gated behind a
 * fake "locked" state.
 */

import { Link, useParams } from 'react-router-dom'
import { useLectures, useModules, useReels, useYouTubeInfo } from '@/api/hooks'
import { formatDuration } from '@/lib/format'
import {
  Badge, ButtonLink, Container, ErrorState, Skeleton,
} from '@/components/ui'
import { cn } from '@/lib/cn'

export default function Lecture() {
  const { jobId } = useParams()
  const { data: modules, isPending, isError, error, refetch } = useModules(jobId)
  const { data: lectures } = useLectures()
  const { data: youtube } = useYouTubeInfo(jobId)
  const { data: reelData } = useReels(jobId)

  const lecture = lectures?.find((l) => l.job_id === jobId)
  const reelsByModule = new Map(
    (reelData?.reels ?? [])
      .filter((r) => r.reel_status === 'done')
      .map((r) => [r.module_id, r]),
  )

  const totalSeconds =
    modules?.reduce((n, m) => n + (m.duration_sec || 0), 0) ?? 0

  return (
    <Container>
      <div className="py-8 md:py-12">
        <nav className="mb-6 text-xs text-muted">
          <Link to="/explore" className="transition-colors hover:text-green">
            Explore
          </Link>
        </nav>

        <header className="max-w-3xl">
          <p className="eyebrow">Learning path</p>
          <h1 className="mt-3 font-display text-[1.75rem] font-extrabold leading-[1.15] tracking-[-0.03em] text-ink sm:text-3xl">
            {lecture?.title ?? 'Lecture'}
          </h1>
          <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-micro text-muted">
            {modules && <span>{modules.length} CONCEPTS</span>}
            {totalSeconds > 0 && <span>· {formatDuration(totalSeconds)} OF LECTURE</span>}
            {youtube?.source === 'youtube' && <span>· FROM YOUTUBE</span>}
          </p>
          <p className="mt-4 max-w-2xl text-sm text-muted2">
            These are the concepts MAROS found in this lecture, in the order it
            teaches them. Start anywhere — nothing is locked.
          </p>
        </header>

        <div className="mt-12 max-w-3xl">
          {isPending && (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-20" />
              ))}
            </div>
          )}

          {isError && (
            <ErrorState
              title="Could not load this lecture"
              detail={error instanceof Error ? error.message : undefined}
              onRetry={() => void refetch()}
            />
          )}

          {modules?.map((m, i) => {
            const isLast = i === modules.length - 1
            const reel = reelsByModule.get(m.module_id)

            return (
              <div key={m.module_id} className="relative flex gap-4 sm:gap-6">
                {/* Rail: the node and the connector to the next concept. */}
                <div className="flex flex-col items-center">
                  <span
                    className={cn(
                      'mt-5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border font-mono text-micro',
                      i === 0
                        ? 'border-green bg-green-soft text-green'
                        : 'border-line2 bg-surface text-muted2',
                    )}
                  >
                    {String(m.module_id).padStart(2, '0')}
                  </span>
                  {!isLast && <span className="w-px flex-1 bg-line2" aria-hidden />}
                </div>

                <Link
                  to={`/lecture/${jobId}/module/${m.module_id}`}
                  className={cn(
                    'card-interactive group mb-4 flex-1 p-5',
                    isLast && 'mb-0',
                  )}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <h2 className="min-w-0 flex-1 font-display text-base font-semibold leading-snug text-ink transition-colors group-hover:text-green">
                      {m.concept}
                    </h2>
                    {reel && <Badge tone="green">Meal</Badge>}
                  </div>

                  <p className="mt-3 flex flex-wrap items-center gap-x-3 font-mono text-micro text-muted">
                    <span>{m.start}-{m.end}</span>
                    <span>· {formatDuration(m.duration_sec)}</span>
                  </p>
                </Link>
              </div>
            )
          })}
        </div>

        {modules && modules.length > 0 && (
          <div className="mt-10">
            <ButtonLink
              to={`/lecture/${jobId}/module/${modules[0].module_id}`}
              variant="primary"
              size="lg"
            >
              Start with {modules[0].concept.split(/[,:]/)[0]}
            </ButtonLink>
          </div>
        )}
      </div>
    </Container>
  )
}
