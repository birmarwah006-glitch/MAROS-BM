/**
 * Concept lesson — the most important page in the product.
 *
 * Structure follows the brief's beats, with each one bound to something the
 * backend genuinely returns:
 *
 *   Hook / framing   the concept title, its position in the lecture, timings
 *   Explanation      GET /modules/{job}/{module}/notes  (markdown)
 *   Visual           the Mermaid concept map spliced into those notes
 *   Source clip      GET /modules/{job}/{module}/video  (or a YouTube embed)
 *   Code + run       only when the notes actually contain a code block
 *   Practice         POST /quiz/generate + /quiz/submit
 *   Ask MAROS        POST /chat, grounded with job_id + module_id
 *
 * There is no invented lesson content. If a beat has no data behind it for
 * this module, the beat is not shown.
 */

import { useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useModuleNotes, useModules, useYouTubeInfo } from '@/api/hooks'
import { mediaUrl } from '@/api/client'
import { EXECUTABLE_LANGUAGES } from '@/api/client'
import { formatDuration } from '@/lib/format'
import { Badge, Container, ErrorState, Skeleton } from '@/components/ui'
import { Notes, notesFailed } from '@/components/Notes'
import { Quiz } from '@/components/Quiz'
import { CodeRunner } from '@/components/CodeRunner'
import { TutorPanel } from '@/components/TutorPanel'

/** Pulls the first runnable fenced code block out of the notes, if any. */
function extractRunnableCode(markdown: string) {
  const fence = /```(\w+)\n([\s\S]*?)```/g
  let match: RegExpExecArray | null
  while ((match = fence.exec(markdown))) {
    const lang = match[1].toLowerCase()
    if ((EXECUTABLE_LANGUAGES as readonly string[]).includes(lang)) {
      return { language: lang, code: match[2].trim() }
    }
  }
  return null
}

function timeToSeconds(t: string) {
  const parts = t.split(':').map(Number)
  if (parts.some(Number.isNaN)) return 0
  return parts.reduce((acc, p) => acc * 60 + p, 0)
}

export default function Lesson() {
  const { jobId, moduleId: moduleIdParam } = useParams()
  const moduleId = Number(moduleIdParam)

  const { data: modules, isPending: modulesPending } = useModules(jobId)
  const {
    data: notesData,
    isPending: notesPending,
    isError: notesError,
    error: notesErrorObj,
    refetch: refetchNotes,
  } = useModuleNotes(jobId, moduleId)
  const { data: youtube } = useYouTubeInfo(jobId)

  const module = modules?.find((m) => m.module_id === moduleId)
  const index = modules?.findIndex((m) => m.module_id === moduleId) ?? -1
  const prev = index > 0 ? modules?.[index - 1] : undefined
  const next =
    index >= 0 && modules && index < modules.length - 1
      ? modules[index + 1]
      : undefined

  const notes = notesData?.notes ?? ''
  const runnable = useMemo(() => extractRunnableCode(notes), [notes])

  // A YouTube-sourced job has no downloaded video for the clip endpoint in
  // some cases, but the manifest clip is cut locally either way. Prefer the
  // local clip; fall back to a timestamped YouTube embed.
  const startSec = module ? timeToSeconds(module.start) : 0

  if (!jobId || Number.isNaN(moduleId)) {
    return (
      <Container>
        <div className="py-20">
          <ErrorState title="That lesson link is not valid" />
        </div>
      </Container>
    )
  }

  return (
    <Container>
      <div className="py-8 md:py-12">
        {/* ── Hook / framing ─────────────────────────────────────────── */}
        <nav className="mb-6 flex items-center gap-2 text-xs text-muted">
          <Link to="/explore" className="transition-colors hover:text-green">
            Explore
          </Link>
          <span>/</span>
          <Link
            to={`/lecture/${jobId}`}
            className="truncate transition-colors hover:text-green"
          >
            Learning path
          </Link>
        </nav>

        <header className="max-w-3xl">
          {modulesPending ? (
            <Skeleton className="h-10 w-2/3" />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-micro text-green">
                  CONCEPT {String(moduleId).padStart(2, '0')}
                  {modules && ` OF ${String(modules.length).padStart(2, '0')}`}
                </span>
                {module && (
                  <Badge tone="neutral">
                    {formatDuration(module.duration_sec)}
                  </Badge>
                )}
              </div>
              <h1 className="mt-3 font-display text-[1.75rem] font-extrabold leading-[1.15] tracking-[-0.03em] text-ink sm:text-3xl">
                {module?.concept ?? 'Concept'}
              </h1>
              {module && (
                <p className="mt-2 font-mono text-micro text-muted">
                  FROM {module.start} TO {module.end} IN THE LECTURE
                </p>
              )}
            </>
          )}
        </header>

        <div className="mt-10 grid gap-10 lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start">
          <div className="min-w-0 space-y-12">
            {/* ── Source clip ──────────────────────────────────────── */}
            {module && (
              <section>
                <p className="eyebrow mb-3">Watch the moment it is taught</p>
                <div className="overflow-hidden rounded border border-line bg-surface2">
                  <video
                    src={mediaUrl.moduleVideo(jobId, moduleId)}
                    controls
                    preload="metadata"
                    className="aspect-video w-full bg-black"
                  />
                </div>
                {youtube?.source === 'youtube' && youtube.video_id && (
                  <a
                    href={`https://youtu.be/${youtube.video_id}?t=${startSec}`}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 inline-block text-xs text-muted2 transition-colors hover:text-green"
                  >
                    Open the full lecture on YouTube at {module.start} →
                  </a>
                )}
              </section>
            )}

            {/* ── Explanation + concept map ────────────────────────── */}
            <section>
              <p className="eyebrow mb-4">The concept</p>
              {notesPending && (
                <div className="space-y-3">
                  <Skeleton className="h-5 w-1/3" />
                  <Skeleton className="h-24 w-full" />
                  <Skeleton className="h-40 w-full" />
                </div>
              )}
              {notesError && (
                <ErrorState
                  title="Could not load the notes"
                  detail={
                    notesErrorObj instanceof Error
                      ? notesErrorObj.message
                      : undefined
                  }
                  onRetry={() => void refetchNotes()}
                />
              )}
              {notesData && <Notes notes={notes} concept={module?.concept} />}
            </section>

            {/* ── Code, only when the material actually has code ───── */}
            {runnable && (
              <section>
                <p className="eyebrow mb-4">Run it yourself</p>
                <CodeRunner
                  initialCode={runnable.code}
                  language={runnable.language}
                  title={`${runnable.language} example`}
                />
              </section>
            )}

            {/* ── Practice ─────────────────────────────────────────── */}
            <section>
              <p className="eyebrow mb-4">Practice</p>
              <Quiz jobId={jobId} moduleId={moduleId} />
            </section>

            {/* ── Continue ─────────────────────────────────────────── */}
            <nav className="grid gap-3 border-t border-line pt-6 sm:grid-cols-2">
              {prev ? (
                <Link
                  to={`/lecture/${jobId}/module/${prev.module_id}`}
                  className="card-interactive p-4"
                >
                  <span className="font-mono text-micro text-muted">
                    ← PREVIOUS
                  </span>
                  <p className="mt-1.5 text-sm font-medium text-ink">
                    {prev.concept}
                  </p>
                </Link>
              ) : (
                <div />
              )}
              {next && (
                <Link
                  to={`/lecture/${jobId}/module/${next.module_id}`}
                  className="card-interactive p-4 sm:text-right"
                >
                  <span className="font-mono text-micro text-green">NEXT →</span>
                  <p className="mt-1.5 text-sm font-medium text-ink">
                    {next.concept}
                  </p>
                </Link>
              )}
            </nav>
          </div>

          {/* ── Tutor ──────────────────────────────────────────────── */}
          <aside className="lg:sticky lg:top-20">
            <TutorPanel
              jobId={jobId}
              moduleId={moduleId}
              concept={module?.concept}
              className="max-h-[70vh]"
            />
            {notesData && notesFailed(notes) && (
              <p className="mt-3 text-xs leading-relaxed text-muted">
                Notes are missing for this concept, but the tutor still has the
                full transcript — ask it anything about the material.
              </p>
            )}
          </aside>
        </div>
      </div>
    </Container>
  )
}
