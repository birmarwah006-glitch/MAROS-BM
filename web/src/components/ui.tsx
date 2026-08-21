/**
 * MAROS design-system primitives.
 *
 * Deliberately restrained, per the brief: no gradients, no glows, no floating
 * blobs. Hierarchy comes from weight, spacing and hairline rules. The green
 * accent is a signal — current, active, correct, primary — never decoration.
 */

import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/lib/cn'

/* ── Button ───────────────────────────────────────────────────────────── */

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger'
type Size = 'sm' | 'md' | 'lg'

const VARIANTS: Record<Variant, string> = {
  primary:
    'bg-green text-accent-ink border-green hover:opacity-90 font-semibold',
  secondary:
    'bg-surface2 text-ink border-line2 hover:border-muted2',
  ghost:
    'bg-transparent text-muted2 border-transparent hover:text-ink hover:bg-surface2',
  danger:
    'bg-red-soft text-red border-red-line hover:bg-transparent',
}

const SIZES: Record<Size, string> = {
  sm: 'h-8 px-3 text-xs gap-1.5',
  md: 'h-10 px-4 text-sm gap-2',
  lg: 'h-12 px-6 text-base gap-2.5',
}

const BUTTON_BASE =
  'inline-flex shrink-0 items-center justify-center rounded border font-display ' +
  'transition-all duration-150 select-none ' +
  'disabled:opacity-40 disabled:pointer-events-none active:scale-[0.98]'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', size = 'md', className, ...rest }, ref) => (
    <button
      ref={ref}
      className={cn(BUTTON_BASE, VARIANTS[variant], SIZES[size], className)}
      {...rest}
    />
  ),
)
Button.displayName = 'Button'

export function ButtonLink({
  to, variant = 'secondary', size = 'md', className, children,
}: {
  to: string; variant?: Variant; size?: Size; className?: string; children: ReactNode
}) {
  return (
    <Link
      to={to}
      className={cn(BUTTON_BASE, VARIANTS[variant], SIZES[size], className)}
    >
      {children}
    </Link>
  )
}

/* ── Badge ────────────────────────────────────────────────────────────── */

type Tone = 'neutral' | 'green' | 'blue' | 'red' | 'orange'

const TONES: Record<Tone, string> = {
  neutral: 'border-line2 text-muted2',
  green: 'border-green-line text-green bg-green-soft',
  blue: 'border-blue-line text-blue bg-blue-soft',
  red: 'border-red-line text-red bg-red-soft',
  orange: 'border-line2 text-orange',
}

export function Badge({
  tone = 'neutral', children, className,
}: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded border px-2 py-0.5',
        'font-mono text-micro uppercase tracking-[0.1em]',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

/* ── Progress ─────────────────────────────────────────────────────────── */

export function ProgressBar({
  value, className, tone = 'green',
}: { value: number; className?: string; tone?: 'green' | 'blue' }) {
  const pct = Math.max(0, Math.min(100, Math.round(value)))
  return (
    <div
      className={cn('h-1 w-full overflow-hidden rounded-full bg-surface2', className)}
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className={cn(
          'h-full rounded-full transition-[width] duration-500 ease-out',
          tone === 'green' ? 'bg-green' : 'bg-blue',
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

/** The blocky progress meter the brief asks for on My Learning. */
export function BlockProgress({ value, blocks = 10 }: { value: number; blocks?: number }) {
  const filled = Math.round((Math.max(0, Math.min(100, value)) / 100) * blocks)
  return (
    <span className="font-mono text-sm tracking-tight" aria-hidden>
      <span className="text-green">{'█'.repeat(filled)}</span>
      <span className="text-line2">{'░'.repeat(blocks - filled)}</span>
    </span>
  )
}

/* ── States ───────────────────────────────────────────────────────────── */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('skeleton', className)} />
}

export function EmptyState({
  title, body, action, icon,
}: { title: string; body?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center rounded border border-dashed border-line2 px-6 py-16 text-center">
      {icon && <div className="mb-4 text-muted">{icon}</div>}
      <p className="font-display text-lg font-semibold text-ink">{title}</p>
      {body && <div className="mt-2 max-w-md text-sm text-muted2">{body}</div>}
      {action && <div className="mt-6">{action}</div>}
    </div>
  )
}

/**
 * Error state. Shows the backend's own `detail` string rather than a generic
 * message — the difference between "code execution isn't configured on the
 * server" and "something went wrong" is the difference between a user who
 * knows what to do and one who does not.
 */
export function ErrorState({
  title = 'Something went wrong', detail, onRetry,
}: { title?: string; detail?: string; onRetry?: () => void }) {
  return (
    <div className="rounded border border-red-line bg-red-soft px-5 py-4">
      <p className="font-display text-sm font-semibold text-red">{title}</p>
      {detail && (
        <p className="mt-1.5 font-mono text-xs leading-relaxed text-muted2">
          {detail}
        </p>
      )}
      {onRetry && (
        <Button size="sm" variant="secondary" className="mt-3" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'inline-block h-4 w-4 animate-spin rounded-full border-2 border-line2 border-t-green',
        className,
      )}
      aria-hidden
    />
  )
}

/* ── Layout ───────────────────────────────────────────────────────────── */

export function Section({
  eyebrow, title, description, children, className,
}: {
  eyebrow?: string; title?: string; description?: ReactNode
  children?: ReactNode; className?: string
}) {
  return (
    <section className={cn('py-14 md:py-20', className)}>
      {eyebrow && <p className="eyebrow mb-3">{eyebrow}</p>}
      {title && (
        <h2 className="font-display text-2xl font-bold text-ink md:text-3xl">
          {title}
        </h2>
      )}
      {description && (
        <p className="mt-3 max-w-2xl text-base text-muted2">{description}</p>
      )}
      {children && <div className="mt-8">{children}</div>}
    </section>
  )
}

export function Container({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('mx-auto w-full max-w-6xl px-5 md:px-8', className)}>
      {children}
    </div>
  )
}
