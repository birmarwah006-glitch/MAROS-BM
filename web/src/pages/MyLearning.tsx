/**
 * My Learning.
 *
 * HONEST LIMITS. The backend has no course-level progress endpoint (GAP 6):
 *   GET /student/mastery    per-concept scores, no course grouping, no roll-up
 *   GET /student/classwork  quiz attempts grouped by module
 * There is no "resume where you left off" and no percent-per-course figure,
 * so none is displayed. What is shown is real mastery data and real quiz
 * history, plus the lectures that actually exist to continue.
 *
 * Both endpoints return 200 with an explanatory `message` when logged out
 * rather than 401, so that path is rendered as a prompt, not an error.
 */

import { Link } from 'react-router-dom'
import { useClasswork, useLectures, useMastery } from '@/api/hooks'
import { isLoggedIn } from '@/api/client'
import {
  BlockProgress, ButtonLink, Container, EmptyState, ProgressBar, Skeleton,
} from '@/components/ui'

function SignedOutNotice({ message }: { message?: string }) {
  return (
    <div className="rounded border border-line bg-surface px-5 py-4">
      <p className="text-sm text-muted2">
        {message ?? 'Sign in to track what you have learned.'}
      </p>
      <p className="mt-2 text-xs text-muted">
        MAROS uses the same account as the classic interface — sign in there and
        come back.{' '}
        <a href="/app/" className="text-green hover:underline">
          Open the classic interface
        </a>
      </p>
    </div>
  )
}

export default function MyLearning() {
  const { data: mastery, isPending: masteryPending } = useMastery()
  const { data: classwork } = useClasswork()
  const { data: lectures, isPending: lecturesPending } = useLectures()

  const signedIn = isLoggedIn()
  const scores = mastery?.mastery ?? []

  return (
    <Container>
      <div className="py-10 md:py-14">
        <header>
          <p className="eyebrow">My learning</p>
          <h1 className="mt-3 font-display text-[1.75rem] font-extrabold tracking-[-0.03em] text-ink sm:text-3xl">
            Where you are
          </h1>
        </header>

        {/* ── Continue ─────────────────────────────────────────────── */}
        <section className="mt-10">
          <h2 className="eyebrow mb-4">Continue learning</h2>

          {lecturesPending && <Skeleton className="h-24" />}

          {!lecturesPending && (lectures?.length ?? 0) === 0 && (
            <EmptyState
              title="Nothing to continue yet"
              body="Add a lecture and MAROS will build a learning path from it."
              action={
                <ButtonLink to="/add" variant="primary">
                  Add a lecture
                </ButtonLink>
              }
            />
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            {lectures?.map((l) => (
              <Link
                key={l.job_id}
                to={`/lecture/${l.job_id}`}
                className="card-interactive group p-5"
              >
                <p className="font-display text-base font-semibold text-ink transition-colors group-hover:text-green">
                  {l.title}
                </p>
                <p className="mt-1 font-mono text-micro text-muted">
                  {l.total_modules} CONCEPTS
                </p>
                <div className="mt-4 flex items-center gap-3">
                  <ProgressBar value={0} className="flex-1" />
                  <span className="font-mono text-micro text-muted">
                    NOT STARTED
                  </span>
                </div>
              </Link>
            ))}
          </div>

          {(lectures?.length ?? 0) > 0 && (
            <p className="mt-3 text-xs text-muted">
              Per-lecture completion is not tracked by the backend yet, so these
              bars show no progress rather than a guess.
            </p>
          )}
        </section>

        {/* ── Mastery ──────────────────────────────────────────────── */}
        <section className="mt-14">
          <h2 className="eyebrow mb-4">Concept mastery</h2>

          {!signedIn ? (
            <SignedOutNotice message={mastery?.message} />
          ) : masteryPending ? (
            <Skeleton className="h-32" />
          ) : scores.length === 0 ? (
            <EmptyState
              title="No mastery data yet"
              body="Take a quiz on any concept and MAROS starts tracking what you know and what you do not."
              action={
                <ButtonLink to="/explore" variant="secondary">
                  Find a concept
                </ButtonLink>
              }
            />
          ) : (
            <ul className="space-y-3">
              {scores.map((m, i) => (
                <li
                  key={i}
                  className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded border border-line bg-surface px-5 py-4"
                >
                  <span className="min-w-0 flex-1 text-sm text-ink">
                    {m.concept_name}
                  </span>
                  <BlockProgress value={(m.score ?? 0) * 100} />
                  <span className="w-12 text-right font-mono text-micro text-muted2">
                    {Math.round((m.score ?? 0) * 100)}%
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ── Quiz history ─────────────────────────────────────────── */}
        {signedIn && (classwork?.quizzes.length ?? 0) > 0 && (
          <section className="mt-14">
            <h2 className="eyebrow mb-4">Recent practice</h2>
            <ul className="space-y-2">
              {classwork!.quizzes.slice(0, 8).map((q, i) => (
                <li
                  key={i}
                  className="flex flex-wrap items-center justify-between gap-3 rounded border border-line bg-surface px-5 py-3"
                >
                  <span className="font-mono text-xs text-muted2">
                    {q.module_id ?? 'quiz'}
                  </span>
                  <span className="text-sm text-ink">
                    {q.correct}/{q.total} correct
                  </span>
                  <span className="font-mono text-micro text-muted">
                    {q.taken_at
                      ? new Date(q.taken_at).toLocaleDateString()
                      : ''}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </Container>
  )
}
