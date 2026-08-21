/**
 * Profile.
 *
 * Thin on purpose. There is no profile endpoint — the only identity the
 * backend exposes is the Supabase user id echoed by /student/mastery, and
 * sign-in itself lives in the existing /app frontend. Rather than build a
 * parallel auth flow, this page reports the real session state and links
 * across.
 */

import { useMastery } from '@/api/hooks'
import { getSession, isLoggedIn } from '@/api/client'
import { Container, EmptyState } from '@/components/ui'

export default function Profile() {
  const signedIn = isLoggedIn()
  const { data: mastery } = useMastery()
  const session = getSession()

  return (
    <Container>
      <div className="mx-auto max-w-2xl py-10 md:py-14">
        <header>
          <p className="eyebrow">Profile</p>
          <h1 className="mt-3 font-display text-[1.75rem] font-extrabold tracking-[-0.03em] text-ink sm:text-3xl">
            Your account
          </h1>
        </header>

        <div className="mt-8">
          {signedIn ? (
            <dl className="divide-y divide-line rounded border border-line bg-surface">
              <div className="flex items-center justify-between px-5 py-4">
                <dt className="text-sm text-muted2">Status</dt>
                <dd className="font-mono text-xs text-green">SIGNED IN</dd>
              </div>
              {mastery?.student_id && (
                <div className="flex items-center justify-between gap-4 px-5 py-4">
                  <dt className="text-sm text-muted2">Student id</dt>
                  <dd className="truncate font-mono text-xs text-muted2">
                    {mastery.student_id}
                  </dd>
                </div>
              )}
              <div className="flex items-center justify-between px-5 py-4">
                <dt className="text-sm text-muted2">Concepts tracked</dt>
                <dd className="font-mono text-xs text-ink">
                  {mastery?.mastery.length ?? 0}
                </dd>
              </div>
              <div className="px-5 py-4">
                <button
                  type="button"
                  onClick={() => {
                    localStorage.removeItem('maros_session')
                    sessionStorage.removeItem('maros_prof_token')
                    window.location.reload()
                  }}
                  className="text-sm text-red transition-opacity hover:opacity-80"
                >
                  Sign out
                </button>
              </div>
            </dl>
          ) : (
            <EmptyState
              title="You are not signed in"
              body={
                <>
                  Concepts and quizzes work signed out — your answers just are
                  not saved. Sign in through the classic interface to track
                  mastery across sessions.
                </>
              }
              action={
                <a
                  href="/app/"
                  className="inline-flex h-10 items-center rounded border border-green bg-green px-4 text-sm font-semibold text-accent-ink"
                >
                  Open the classic interface
                </a>
              }
            />
          )}
        </div>

        {session?.access_token && (
          <p className="mt-4 text-xs text-muted">
            Session is shared with the classic MAROS interface.
          </p>
        )}
      </div>
    </Container>
  )
}
