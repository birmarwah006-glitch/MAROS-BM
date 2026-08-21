/**
 * Mermaid diagram renderer.
 *
 * Chipper generates one concept diagram per module and splices it into the
 * notes markdown at the %%CONCEPT_MAP%% token, already linted server-side
 * (chipper._lint_mermaid). Even so, render failures are caught and shown as
 * the diagram source rather than crashing the lesson — a broken diagram must
 * never take the notes down with it.
 */

import { useEffect, useId, useRef, useState } from 'react'

/**
 * mermaid is ~1MB of JavaScript. Importing it at module scope drags it into
 * the main bundle for every page, including the mobile Explore feed which
 * never renders a diagram. It is loaded on demand instead, the first time a
 * concept map is actually on screen.
 */
type MermaidApi = typeof import('mermaid').default
let mermaidPromise: Promise<MermaidApi> | null = null

function loadMermaid(): Promise<MermaidApi> {
  mermaidPromise ??= import('mermaid').then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'base',
      fontFamily: "'DM Mono', ui-monospace, monospace",
      themeVariables: {
        background: '#0a0a0a',
        primaryColor: '#111111',
        primaryTextColor: '#f0ede6',
        primaryBorderColor: 'rgba(255,255,255,0.12)',
        lineColor: '#555550',
        secondaryColor: '#111111',
        tertiaryColor: '#0a0a0a',
        fontSize: '14px',
      },
    })
    return mermaid
  })
  return mermaidPromise
}

export function Mermaid({ chart }: { chart: string }) {
  const reactId = useId()
  const id = `mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, '')}`
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false

    loadMermaid()
      .then((mermaid) => mermaid.render(id, chart))
      .then(({ svg }) => {
        if (cancelled || !ref.current) return
        ref.current.innerHTML = svg
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Diagram failed to render')
        // Mermaid leaves its failed render node in the DOM on error.
        document.getElementById(id)?.remove()
      })

    return () => {
      cancelled = true
    }
  }, [chart, id])

  if (error) {
    return (
      <figure className="my-6 overflow-hidden rounded border border-line">
        <figcaption className="border-b border-line bg-surface2 px-4 py-2 font-mono text-micro uppercase tracking-[0.1em] text-muted2">
          Concept map — could not render
        </figcaption>
        <pre className="overflow-x-auto p-4 font-mono text-xs leading-relaxed text-muted2">
          {chart}
        </pre>
      </figure>
    )
  }

  return (
    <figure className="my-6 overflow-hidden rounded border border-line bg-surface">
      <figcaption className="border-b border-line px-4 py-2 font-mono text-micro uppercase tracking-[0.1em] text-muted2">
        Concept map
      </figcaption>
      <div className="overflow-x-auto p-4">
        <div ref={ref} className="flex min-w-fit justify-center [&_svg]:h-auto [&_svg]:max-w-none" />
      </div>
    </figure>
  )
}
