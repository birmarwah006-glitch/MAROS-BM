/**
 * Processing / analysis page.
 *
 * Polls useJobProgress, which survives the backend's in-memory job store
 * (GAP 7): GET /jobs/{id} 404s after a server restart, so the hook falls back
 * to the on-disk manifest. A 404 is therefore reported as "status unknown",
 * never as a failure — losing the job record mid-transcription is normal in
 * this deployment and must not look like the lecture was lost.
 */

import { useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  STAGE_LABELS, TOTAL_STAGES, stageIndex, useJobProgress,
} from '@/api/hooks'
import type { JobStatus } from '@/api/types'
import {
  ButtonLink, Container, ErrorState, ProgressBar, Spinner,
} from '@/components/ui'
import { cn } from '@/lib/cn'

const STAGES: JobStatus[] = [
  'transcribing', 'segmenting', 'cutting', 'summarizing',
]

export default function Processing() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const { data, isError, error, refetch } = useJobProgress(jobId)

  const manifest = data?.manifest
  const done = data?.status === 'done' && manifest

  // Give the "found N concepts" reveal a beat before moving on, rather than
  // yanking the page out from under the user.
  useEffect(() => {
    if (!done || !manifest) return
    const t = setTimeout(() => navigate(`/lecture/${jobId}`), 6000)
    return () => clearTimeout(t)
  }, [done, manifest, jobId, navigate])

  const current = stageIndex(data?.status ?? 'unknown')

  return (
    <Container>
      <div className="mx-auto max-w-2xl py-12 md:py-20">
        {isError ? (
          <ErrorState
            title="Could not check on this lecture"
            detail={error instanceof Error ? error.message : undefined}
            onRetry={() => void refetch()}
          />
        ) : done && manifest ? (
          <div className="animate-fade-up">
            <p className="eyebrow">Analysis complete</p>
            <h1 className="mt-3 font-display text-[1.75rem] font-extrabold tracking-[-0.03em] text-ink sm:text-3xl">
              MAROS found {manifest.total_modules} concept
              {manifest.total_modules === 1 ? '' : 's'}
            </h1>

            <ol className="mt-8 space-y-2">
              {manifest.modules.map((m) => (
                <li
                  key={m.module_id}
                  className="flex items-center gap-3 rounded border border-line bg-surface px-4 py-3"
                >
                  <span className="font-mono text-micro text-green">
                    {String(m.module_id).padStart(2, '0')}
                  </span>
                  <span className="flex-1 text-sm text-ink">{m.concept}</span>
                  <span className="hidden font-mono text-micro text-muted sm:inline">
                    {m.start}
                  </span>
                </li>
              ))}
            </ol>

            <div className="mt-8 flex flex-wrap gap-3">
              <ButtonLink to={`/lecture/${jobId}`} variant="primary" size="lg">
                Start learning
              </ButtonLink>
              <ButtonLink to="/explore" variant="ghost" size="lg">
                Back to Explore
              </ButtonLink>
            </div>
          </div>
        ) : data?.status === 'failed' ? (
          <div>
            <p className="eyebrow">Processing failed</p>
            <h1 className="mt-3 font-display text-2xl font-extrabold tracking-[-0.03em] text-ink">
              MAROS could not finish this lecture
            </h1>
            <div className="mt-5">
              <ErrorState
                title="The pipeline stopped"
                detail={data.error ?? 'No reason was reported by the server.'}
              />
            </div>
            <div className="mt-6">
              <ButtonLink to="/add" variant="secondary">
                Try another lecture
              </ButtonLink>
            </div>
          </div>
        ) : (
          <div>
            <p className="eyebrow">Analysing</p>
            <h1 className="mt-3 font-display text-[1.75rem] font-extrabold tracking-[-0.03em] text-ink sm:text-3xl">
              MAROS is working through your lecture
            </h1>
            <p className="mt-3 text-sm text-muted2">
              This takes a while for a full-length recording. You can leave this
              page — progress is kept on the server.
            </p>

            <div className="mt-8">
              <ProgressBar value={data?.progress ?? 0} />
              <p className="mt-2 font-mono text-micro text-muted">
                {data?.progress ?? 0}% · STEP {Math.min(current, TOTAL_STAGES)} OF{' '}
                {TOTAL_STAGES}
              </p>
            </div>

            <ol className="mt-8 space-y-1">
              {STAGES.map((stage) => {
                const idx = stageIndex(stage)
                const state =
                  current > idx ? 'done' : current === idx ? 'active' : 'pending'
                return (
                  <li
                    key={stage}
                    className={cn(
                      'flex items-center gap-3 rounded px-3 py-2.5 transition-colors',
                      state === 'active' && 'bg-surface2',
                    )}
                  >
                    <span
                      className={cn(
                        'flex h-5 w-5 items-center justify-center rounded-full border font-mono text-[9px]',
                        state === 'done' && 'border-green bg-green text-accent-ink',
                        state === 'active' && 'border-green text-green',
                        state === 'pending' && 'border-line2 text-muted',
                      )}
                    >
                      {state === 'done' ? '✓' : ''}
                    </span>
                    <span
                      className={cn(
                        'text-sm',
                        state === 'pending' ? 'text-muted' : 'text-ink',
                      )}
                    >
                      {STAGE_LABELS[stage]}
                    </span>
                    {state === 'active' && <Spinner className="ml-auto" />}
                  </li>
                )
              })}
            </ol>

            {data?.recoveredFromDisk && (
              <p className="mt-6 rounded border border-line bg-surface2 px-4 py-3 text-xs leading-relaxed text-muted2">
                The server no longer holds a live record for this job — it was
                most likely restarted. The work itself is written to disk as it
                completes, so this page keeps checking for the finished result.
              </p>
            )}

            <p className="mt-6 text-xs text-muted">
              Nothing to watch?{' '}
              <Link to="/explore" className="text-green hover:underline">
                Explore concepts already extracted
              </Link>
              .
            </p>
          </div>
        )}
      </div>
    </Container>
  )
}
