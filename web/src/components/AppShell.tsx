/**
 * App chrome. Desktop gets a top bar; mobile gets a bottom tab bar, because
 * the brief asks for a discovery experience that is genuinely good on a
 * phone rather than a shrunken desktop layout.
 */

import { NavLink, Link, Outlet, useLocation } from 'react-router-dom'
import { cn } from '@/lib/cn'
import { Container } from './ui'

const NAV = [
  { to: '/feed', label: 'Meals' },
  { to: '/explore', label: 'Explore' },
  { to: '/learning', label: 'My Learning' },
  { to: '/add', label: 'Add lecture' },
  { to: '/ask', label: 'Ask MAROS' },
]

function Wordmark() {
  return (
    <Link to="/" className="group flex items-center gap-2" aria-label="MAROS home">
      <span
        className="h-3 w-3 shrink-0 rounded-[3px] bg-green transition-transform duration-200 group-hover:rotate-45"
        aria-hidden
      />
      <span className="font-display text-base font-extrabold tracking-[-0.02em] text-ink">
        MAROS
      </span>
    </Link>
  )
}

export function AppShell() {
  const { pathname } = useLocation()
  const isLanding = pathname === '/'

  return (
    <div className="flex min-h-dvh flex-col bg-bg">
      <header
        className={cn(
          'sticky top-0 z-40 border-b border-line',
          'bg-bg/80 backdrop-blur-md supports-[backdrop-filter]:bg-bg/70',
        )}
      >
        <Container>
          <div className="flex h-14 items-center justify-between gap-6">
            <Wordmark />

            <nav className="hidden items-center gap-1 md:flex">
              {NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      'rounded px-3 py-1.5 text-sm transition-colors duration-150',
                      isActive
                        ? 'bg-surface2 text-ink'
                        : 'text-muted2 hover:text-ink',
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>

            {isLanding ? (
              <Link
                to="/feed"
                className="rounded border border-green bg-green px-3.5 py-1.5 text-xs font-semibold text-accent-ink transition-opacity hover:opacity-90"
              >
                Start learning
              </Link>
            ) : (
              <Link
                to="/profile"
                className="flex h-8 w-8 items-center justify-center rounded-full border border-line2 font-mono text-xs text-muted2 transition-colors hover:border-muted2 hover:text-ink"
                aria-label="Profile"
              >
                M
              </Link>
            )}
          </div>
        </Container>
      </header>

      <main className="flex-1 pb-20 md:pb-0">
        <Outlet />
      </main>

      {/* Mobile tab bar. Hidden on the landing page, which is a scroll story. */}
      {!isLanding && (
        <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-bg/95 backdrop-blur-md md:hidden">
          <div
            className="grid grid-cols-5"
            style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
          >
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'flex flex-col items-center gap-1 py-2.5 text-[11px] transition-colors',
                    isActive ? 'text-green' : 'text-muted2',
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <span
                      className={cn(
                        'h-1 w-1 rounded-full transition-colors',
                        isActive ? 'bg-green' : 'bg-transparent',
                      )}
                      aria-hidden
                    />
                    {item.label}
                  </>
                )}
              </NavLink>
            ))}
          </div>
        </nav>
      )}

      <footer className="hidden border-t border-line py-8 md:block">
        <Container>
          <div className="flex items-center justify-between text-xs text-muted">
            <span>MAROS — visual learning for computer science.</span>
            <a
              href="/app/"
              className="font-mono transition-colors hover:text-muted2"
            >
              Classic interface
            </a>
          </div>
        </Container>
      </footer>
    </div>
  )
}
