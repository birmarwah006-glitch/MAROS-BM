/**
 * Ask MAROS — contextual tutor.
 *
 * Backed by POST /chat. The endpoint takes {job_id, module_id} for grounding,
 * which is exactly the context a lecture module has, so the quick actions
 * below are real rather than decorative.
 *
 * NOTE: /chat has no `concept_id` parameter and no "concept" mode (GAP 4).
 * That only matters for catalogue concepts, which do not exist yet — every
 * concept in V1 belongs to a lecture module and therefore has job/module ids.
 *
 * Replies come back with LaTeX in them, so answers render through the same
 * markdown+math pipeline the notes use.
 */

import { useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { api, ApiError } from '@/api/client'
import type { ChatMessage } from '@/api/types'
import { Button, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

/** The brief's action bar. Each one is just a well-formed prompt — no
 *  invented endpoint sits behind them. */
const QUICK_ACTIONS = [
  { label: 'Explain simpler', prompt: 'Explain this concept in the simplest terms you can, as if I have never seen it before.' },
  { label: 'Give me an analogy', prompt: 'Give me one concrete everyday analogy for this concept, and say exactly where the analogy breaks down.' },
  { label: 'Show me visually', prompt: 'Describe this concept as a diagram: what the boxes are, what the arrows mean, and what changes step by step.' },
  { label: 'Show me code', prompt: 'Show me a short, runnable code example that demonstrates this concept, and explain each line briefly.' },
  { label: 'Quiz me', prompt: 'Ask me one hard question about this concept. Wait for my answer before telling me whether I am right.' },
]

export function TutorPanel({
  jobId,
  moduleId,
  concept,
  className,
}: {
  jobId?: string
  moduleId?: number
  concept?: string
  className?: string
}) {
  const [history, setHistory] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  async function send(message: string) {
    if (!message.trim() || pending) return
    setError(null)
    setPending(true)

    const outgoing: ChatMessage = { role: 'user', content: message }
    const nextHistory = [...history, outgoing]
    setHistory(nextHistory)
    setInput('')

    try {
      const reply = await api.chat({
        message,
        job_id: jobId ?? null,
        module_id: moduleId ?? null,
        history,
        mode: 'videos',
      })
      setHistory([...nextHistory, reply])
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'The tutor did not respond.')
      setHistory(nextHistory)
    } finally {
      setPending(false)
      requestAnimationFrame(() =>
        endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }),
      )
    }
  }

  return (
    <div
      data-testid="tutor"
      className={cn('flex flex-col rounded border border-line bg-surface', className)}
    >
      <header className="border-b border-line px-5 py-4">
        <p className="font-display text-base font-semibold text-ink">Ask MAROS</p>
        <p className="mt-0.5 text-xs text-muted2">
          {concept
            ? `Grounded in "${concept}" and the transcript it came from.`
            : 'Ask anything about what you are learning.'}
        </p>
      </header>

      <div className="flex flex-wrap gap-2 border-b border-line px-5 py-3">
        {QUICK_ACTIONS.map((action) => (
          <button
            key={action.label}
            type="button"
            disabled={pending}
            onClick={() => void send(action.prompt)}
            className="rounded border border-line2 px-2.5 py-1 text-xs text-muted2 transition-colors hover:border-green-line hover:text-green disabled:opacity-40"
          >
            {action.label}
          </button>
        ))}
      </div>

      <div className="min-h-[8rem] flex-1 space-y-4 overflow-y-auto px-5 py-4">
        {history.length === 0 && !pending && (
          <p className="text-sm text-muted">
            Pick an action above, or ask your own question.
          </p>
        )}

        {history.map((m, i) => (
          <div
            key={i}
            className={cn(
              'text-sm leading-relaxed',
              m.role === 'user' ? 'text-ink' : 'text-muted2',
            )}
          >
            <p className="eyebrow mb-1.5">
              {m.role === 'user' ? 'You' : 'MAROS'}
            </p>
            {m.role === 'user' ? (
              <p className="whitespace-pre-wrap">{m.content}</p>
            ) : (
              <div className="[&_code]:rounded [&_code]:bg-surface2 [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.85em] [&_code]:text-green [&_li]:my-1 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:border [&_pre]:border-line [&_pre]:bg-surface2 [&_pre]:p-3 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
                <Markdown
                  remarkPlugins={[remarkGfm, remarkMath]}
                  rehypePlugins={[rehypeKatex]}
                >
                  {m.content}
                </Markdown>
              </div>
            )}
          </div>
        ))}

        {pending && (
          <p className="flex items-center gap-2 text-sm text-muted">
            <Spinner /> Thinking
          </p>
        )}

        {error && (
          <p className="rounded border border-red-line bg-red-soft px-3 py-2 text-xs text-red">
            {error}
          </p>
        )}

        <div ref={endRef} />
      </div>

      <form
        className="flex gap-2 border-t border-line px-5 py-3"
        onSubmit={(e) => {
          e.preventDefault()
          void send(input)
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask about this concept"
          className="flex-1 rounded border border-line bg-surface2 px-3 py-2 text-sm text-ink placeholder:text-muted focus:border-line2 focus:outline-none"
        />
        <Button type="submit" size="sm" variant="primary" disabled={pending || !input.trim()}>
          Ask
        </Button>
      </form>
    </div>
  )
}
