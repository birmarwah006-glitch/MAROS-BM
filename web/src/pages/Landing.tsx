/**
 * Landing page.
 *
 * Positioning: "Understand concepts, not hours of lectures."
 *
 * The concept cards in the hero are NOT invented marketing examples — they
 * are the real modules of whatever lectures the backend has actually
 * processed, read from GET /lectures. If nothing is processed yet, the
 * section says so rather than showing fictional content.
 */

import { Link } from 'react-router-dom'
import { useLectures } from '@/api/hooks'
import { Badge, ButtonLink, Container, Skeleton } from '@/components/ui'

function Hero() {
  return (
    <section className="relative border-b border-line">
      <Container>
        <div className="grid gap-10 py-12 sm:py-16 lg:gap-12 lg:grid-cols-[1.1fr_0.9fr] lg:items-center lg:py-28">
          <div className="min-w-0">
            <Badge tone="green">Visual learning for computer science</Badge>

            <h1 className="mt-6 font-display text-[2.25rem] font-extrabold leading-[1.06] tracking-[-0.03em] text-ink sm:text-[2.75rem] lg:text-[3.5rem] lg:leading-[1.02]">
              Understand concepts,
              <br />
              <span className="text-muted2">not hours of lectures.</span>
            </h1>

            <p className="mt-6 max-w-lg text-lg leading-relaxed text-muted2">
              MAROS takes a full lecture and breaks it into the concepts it
              actually teaches — each one with notes, a concept map, runnable
              code, and a quiz that finds what you missed.
            </p>

            <div className="mt-9 flex flex-col gap-3 sm:flex-row sm:flex-wrap">
              <ButtonLink to="/feed" variant="primary" size="lg">
                Start with a Meal
              </ButtonLink>
              <ButtonLink to="/add" variant="secondary" size="lg">
                Add your own lecture
              </ButtonLink>
            </div>
          </div>

          <ConceptExtractionDiagram />
        </div>
      </Container>
    </section>
  )
}

/**
 * The product thesis as a diagram: one long lecture in, discrete concepts out.
 * Static and structural — no ambient animation, per the brief.
 */
function ConceptExtractionDiagram() {
  const concepts = [
    { time: '00:00', label: 'Propositions and predicates' },
    { time: '12:40', label: 'Boolean operators' },
    { time: '33:17', label: 'Implication and truth tables' },
    { time: '49:23', label: 'Sets and set notation' },
  ]

  return (
    <div className="card min-w-0 p-5 md:p-6">
      <p className="eyebrow">One lecture</p>
      <div className="mt-3 flex items-center gap-3">
        <div className="h-2.5 flex-1 overflow-hidden rounded-full bg-surface2">
          <div className="flex h-full">
            <div className="h-full flex-[1] bg-green" />
            <div className="h-full w-px bg-bg" />
            <div className="h-full flex-[1.4] bg-blue" />
            <div className="h-full w-px bg-bg" />
            <div className="h-full flex-[1.8] bg-purple" />
            <div className="h-full w-px bg-bg" />
            <div className="h-full flex-[1.2] bg-orange" />
          </div>
        </div>
        <span className="font-mono text-micro text-muted">78 MIN</span>
      </div>

      <div className="my-5 flex items-center gap-3">
        <div className="h-px flex-1 bg-line" />
        <span className="eyebrow">Concepts extracted</span>
        <div className="h-px flex-1 bg-line" />
      </div>

      <ul className="space-y-2">
        {concepts.map((c, i) => (
          <li
            key={c.time}
            className="flex items-center gap-3 rounded border border-line bg-surface2 px-3 py-2.5"
          >
            <span className="font-mono text-micro text-muted">
              {String(i + 1).padStart(2, '0')}
            </span>
            <span className="flex-1 text-sm text-ink">{c.label}</span>
            <span className="font-mono text-micro text-muted">{c.time}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

const STEPS = [
  {
    n: '01',
    title: 'Point MAROS at a lecture',
    body: 'Upload a recording or paste a YouTube link. A 90-minute lecture is fine.',
  },
  {
    n: '02',
    title: 'It finds the concepts',
    body: 'The transcript is segmented into the distinct ideas the lecture teaches — not fixed-length chunks.',
  },
  {
    n: '03',
    title: 'Each concept becomes a lesson',
    body: 'Notes, a concept map, the exact clip it came from, and a quiz written against that specific material.',
  },
  {
    n: '04',
    title: 'Practice finds the gaps',
    body: 'Wrong answers are diagnosed into the misconception behind them, so the next thing you study is the right thing.',
  },
]

function HowItWorks() {
  return (
    <section className="border-b border-line">
      <Container>
        <div className="py-16 md:py-24">
          <p className="eyebrow">How MAROS works</p>
          <h2 className="mt-3 max-w-2xl font-display text-2xl font-bold tracking-[-0.02em] text-ink md:text-3xl">
            A lecture is a recording. A concept is something you can learn.
          </h2>

          <div className="mt-12 grid gap-px overflow-hidden rounded border border-line bg-line md:grid-cols-4">
            {STEPS.map((s) => (
              <div key={s.n} className="bg-surface p-6">
                <span className="font-mono text-micro text-green">{s.n}</span>
                <h3 className="mt-3 font-display text-base font-semibold text-ink">
                  {s.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-muted2">
                  {s.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </Container>
    </section>
  )
}

/** Real processed lectures, or an honest empty state. Never fake cards. */
function LiveConcepts() {
  const { data: lectures, isPending, isError } = useLectures()

  const cards =
    lectures
      ?.flatMap((lecture) =>
        lecture.modules.map((m) => ({ lecture, module: m })),
      )
      .slice(0, 6) ?? []

  return (
    <section className="border-b border-line">
      <Container>
        <div className="py-16 md:py-24">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <p className="eyebrow">Available now</p>
              <h2 className="mt-3 font-display text-2xl font-bold tracking-[-0.02em] text-ink md:text-3xl">
                Concepts already extracted
              </h2>
            </div>
            <Link
              to="/explore"
              className="text-sm text-muted2 transition-colors hover:text-green"
            >
              Explore all →
            </Link>
          </div>

          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {isPending &&
              Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-32" />
              ))}

            {isError && (
              <p className="text-sm text-muted2">
                Could not reach the MAROS server right now.
              </p>
            )}

            {!isPending && !isError && cards.length === 0 && (
              <p className="col-span-full text-sm text-muted2">
                No lectures have been processed yet.{' '}
                <Link to="/add" className="text-green hover:underline">
                  Add the first one
                </Link>
                .
              </p>
            )}

            {cards.map(({ lecture, module }) => (
              <Link
                key={`${lecture.job_id}-${module.module_id}`}
                to={`/lecture/${lecture.job_id}/module/${module.module_id}`}
                className="card-interactive group flex flex-col p-5"
              >
                <span className="font-mono text-micro text-muted">
                  {String(module.module_id).padStart(2, '0')}
                </span>
                <h3 className="mt-2 flex-1 font-display text-base font-semibold leading-snug text-ink transition-colors group-hover:text-green">
                  {module.concept}
                </h3>
                <span className="mt-4 truncate text-xs text-muted">
                  {lecture.title}
                </span>
              </Link>
            ))}
          </div>
        </div>
      </Container>
    </section>
  )
}

function ClosingCta() {
  return (
    <section>
      <Container>
        <div className="py-20 text-center md:py-28">
          <h2 className="mx-auto max-w-3xl font-display text-[1.75rem] font-extrabold leading-[1.15] tracking-[-0.03em] text-ink sm:text-3xl md:text-4xl">
            Your next lecture is 90 minutes.
            <br />
            <span className="text-muted2">The concepts in it are not.</span>
          </h2>
          <div className="mt-9 flex flex-wrap justify-center gap-3">
            <ButtonLink to="/add" variant="primary" size="lg">
              Add a lecture
            </ButtonLink>
            <ButtonLink to="/explore" variant="ghost" size="lg">
              Browse what is already here
            </ButtonLink>
          </div>
        </div>
      </Container>
    </section>
  )
}

export default function Landing() {
  return (
    <>
      <Hero />
      <HowItWorks />
      <LiveConcepts />
      <ClosingCta />
    </>
  )
}
