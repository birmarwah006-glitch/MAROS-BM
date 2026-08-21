/**
 * Renders a module's notes.
 *
 * The notes are markdown produced by chipper._generate_detailed_notes, with a
 * Mermaid concept diagram spliced in. Two real behaviours are handled here:
 *
 *   1. Notes generation fails per module, independently of the diagram. When
 *      it does, chipper writes _NOTES_FAILED_SENTINEL into the body. This is
 *      not hypothetical — 2 of the 6 modules in the only processed lecture
 *      carry it today. The sentinel is detected and reported honestly, while
 *      the concept map (which usually survived) still renders.
 *   2. The LLM emits LaTeX, so math has to render.
 */

import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { Mermaid } from './Mermaid'

/**
 * chipper writes the concept as an H1 (from cut_clips) AND the notes prompt
 * emits it again as an H2, so real notes open with the same title twice. The
 * lesson page already displays it as the page heading, so all leading
 * headings that restate the concept are dropped rather than shown three times.
 */
function stripLeadingTitles(markdown: string, concept?: string) {
  const lines = markdown.split('\n')
  const norm = (v: string) =>
    v.replace(/^#+\s*/, '').replace(/[^a-z0-9]/gi, '').toLowerCase()
  const target = concept ? norm(concept) : null

  let i = 0
  while (i < lines.length) {
    const line = lines[i].trim()
    if (!line) { i++; continue }
    if (!/^#{1,3}\s/.test(line)) break
    // Drop it if it restates the concept, or if no concept was supplied and
    // this is simply the leading title block.
    if (target ? norm(line) === target : i === 0) { i++; continue }
    break
  }
  return lines.slice(i).join('\n').trim()
}

/** GitHub alert syntax. chipper's notes prompt emits all four of these. */
const ALERT_STYLES: Record<string, { label: string; className: string }> = {
  NOTE: { label: 'Note', className: 'border-blue-line bg-blue-soft [&_p]:!text-muted2' },
  TIP: { label: 'Tip', className: 'border-green-line bg-green-soft [&_p]:!text-muted2' },
  IMPORTANT: { label: 'Important', className: 'border-green-line bg-green-soft [&_p]:!text-muted2' },
  WARNING: { label: 'Warning', className: 'border-red-line bg-red-soft [&_p]:!text-muted2' },
  CAUTION: { label: 'Caution', className: 'border-red-line bg-red-soft [&_p]:!text-muted2' },
}

const ALERT_LABEL_COLOR: Record<string, string> = {
  NOTE: 'text-blue',
  TIP: 'text-green',
  IMPORTANT: 'text-green',
  WARNING: 'text-red',
  CAUTION: 'text-red',
}

/** Must match chipper._NOTES_FAILED_SENTINEL. */
const NOTES_FAILED = 'Notes generation failed'

export function notesFailed(notes: string) {
  return notes.includes(NOTES_FAILED)
}

/** Strips the sentinel line so it is never rendered as if it were content. */
function stripSentinel(notes: string) {
  return notes
    .split('\n')
    .filter((line) => !line.includes(NOTES_FAILED))
    .join('\n')
    .trim()
}

export function Notes({ notes, concept }: { notes: string; concept?: string }) {
  const failed = notesFailed(notes)
  const body = stripLeadingTitles(
    failed ? stripSentinel(notes) : notes,
    concept,
  )

  return (
    <div>
      {failed && (
        <div className="mb-6 rounded border border-red-line bg-red-soft px-4 py-3">
          <p className="font-display text-sm font-semibold text-red">
            Written notes are missing for this concept
          </p>
          <p className="mt-1 text-xs leading-relaxed text-muted2">
            The notes step failed when this lecture was processed. Everything
            else below — the concept map, the clip, and the quiz — was
            generated from the transcript and is unaffected.
          </p>
        </div>
      )}

      <article
        className="
          max-w-none font-display text-base leading-[1.75] text-ink
          [&_h1]:mb-4 [&_h1]:mt-0 [&_h1]:font-display [&_h1]:text-2xl [&_h1]:font-bold [&_h1]:tracking-[-0.02em]
          [&_h2]:mb-3 [&_h2]:mt-9 [&_h2]:text-lg [&_h2]:font-bold
          [&_h3]:mb-2 [&_h3]:mt-7 [&_h3]:text-base [&_h3]:font-semibold
          [&_p]:my-4 [&_p]:text-muted2
          [&_strong]:font-semibold [&_strong]:text-ink
          [&_ul]:my-4 [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-5 [&_ul]:text-muted2
          [&_ol]:my-4 [&_ol]:list-decimal [&_ol]:space-y-1.5 [&_ol]:pl-5 [&_ol]:text-muted2
          [&_li::marker]:text-muted
          [&_a]:text-green [&_a]:underline [&_a]:underline-offset-2
          [&_blockquote]:my-5 [&_blockquote]:border-l-2 [&_blockquote]:border-green-line [&_blockquote]:bg-green-soft [&_blockquote]:py-2 [&_blockquote]:pl-4 [&_blockquote]:pr-3
          [&_hr]:my-8 [&_hr]:border-line
          [&_table]:my-5 [&_table]:w-full [&_table]:border-collapse [&_table]:text-sm
          [&_th]:border [&_th]:border-line [&_th]:bg-surface2 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold
          [&_td]:border [&_td]:border-line [&_td]:px-3 [&_td]:py-2 [&_td]:text-muted2
          [&_code]:rounded [&_code]:bg-surface2 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-[0.85em] [&_code]:text-green
        "
      >
        <Markdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={{
            code(props) {
              const { className, children, ...rest } = props
              const language = /language-(\w+)/.exec(className ?? '')?.[1]
              const source = String(children).replace(/\n$/, '')

              if (language === 'mermaid') return <Mermaid chart={source} />

              // Inline code keeps the article styling above.
              if (!className) return <code {...rest}>{children}</code>

              return (
                <pre className="my-5 overflow-x-auto rounded border border-line bg-surface2 p-4">
                  <code className="!bg-transparent !p-0 font-mono text-xs leading-relaxed !text-ink">
                    {source}
                  </code>
                </pre>
              )
            },
            // react-markdown wraps block children in <p>; a Mermaid <figure>
            // inside a <p> is invalid HTML and React will warn.
            pre({ children }) {
              return <>{children}</>
            },

            // remark-gfm does not implement GitHub alerts, so "> [!NOTE]"
            // would otherwise render the marker as literal text. chipper's
            // notes use all four kinds, so they are given real treatment.
            blockquote({ children }) {
              const nodes = Array.isArray(children) ? children : [children]
              let kind: string | null = null

              const walk = (value: unknown): unknown => {
                if (typeof value === 'string') {
                  const match = value.match(/^\s*\[!(\w+)\]\s*/)
                  if (match && ALERT_STYLES[match[1].toUpperCase()]) {
                    kind = match[1].toUpperCase()
                    return value.slice(match[0].length)
                  }
                  return value
                }
                if (Array.isArray(value)) return value.map(walk)
                if (
                  value && typeof value === 'object' && 'props' in value
                ) {
                  const el = value as { props?: { children?: unknown } }
                  if (el.props && 'children' in el.props) {
                    return {
                      ...(value as object),
                      props: { ...el.props, children: walk(el.props.children) },
                    }
                  }
                }
                return value
              }

              const cleaned = walk(nodes) as React.ReactNode

              if (!kind) {
                return (
                  <blockquote className="my-5 border-l-2 border-line2 bg-surface2 py-2 pl-4 pr-3">
                    {children}
                  </blockquote>
                )
              }

              const style = ALERT_STYLES[kind]
              return (
                <aside
                  className={`my-5 rounded border px-4 py-3 ${style.className}`}
                >
                  <p
                    className={`mb-1 !mt-0 font-mono text-micro uppercase tracking-[0.12em] ${ALERT_LABEL_COLOR[kind]}`}
                  >
                    {style.label}
                  </p>
                  <div className="[&>p:first-child]:!mt-0 [&>p:last-child]:!mb-0 text-sm">
                    {cleaned}
                  </div>
                </aside>
              )
            },
          }}
        >
          {body}
        </Markdown>
      </article>
    </div>
  )
}
